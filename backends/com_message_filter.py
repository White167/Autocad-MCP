"""An ``IMessageFilter`` for the COM thread, so a refused call is retried by COM.

AutoCAD's message filter refuses an incoming call (``RPC_E_CALL_REJECTED``,
"Call was rejected by callee") while it is busy - switching documents or
layouts, finishing a ``SendCommand``, regenerating. The refused call did not
run. A client that registers its own ``IMessageFilter`` is asked, through
``RetryRejectedCall``, whether to send it again; without one COM fails the
call at once. Autodesk's automation guidance is to register one.

pywin32 has no ``CoRegisterMessageFilter``, so this module builds the
six-entry vtable with ``ctypes`` and registers it on the calling thread, which
must be the single-threaded apartment that makes every AutoCAD call
(``ComBackend``'s executor thread). Measured on AutoCAD 2024 (24.3), where
calls were refused often enough that ``spds_sheet`` failed one run in two
(the refusal landed on ``AddLine``, ``ObjectName``, ``Documents.Count``) and
a per-call-site retry could not cover every call.

A filter only ever retries a call COM reports as *refused*, which by
definition never ran, so it is safe for writes as well as reads. After
``budget_ms`` it gives up and the caller sees ``RPC_E_CALL_REJECTED`` exactly
as before, so the existing handling of that error still applies.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, Structure, byref, c_ubyte, c_ulong, c_ushort, c_void_p

log = logging.getLogger("autocad_mcp")

#: SERVERCALL_RETRYLATER: the callee asks to be called again later.
_SERVERCALL_RETRYLATER = 2
#: HandleInComingCall's answer: process every call made into this thread.
_SERVERCALL_ISHANDLED = 0
#: MessagePending's answer: let COM's default processing handle the message.
_PENDINGMSG_WAITDEFPROCESS = 2
#: RetryRejectedCall's "give up" answer ((DWORD)-1).
_CANCEL = 0xFFFFFFFF
_S_OK = 0
_E_NOINTERFACE = -2147467262

#: Total time one call may be retried for, and the pause between attempts
#: (COM treats 100 or more as "wait this many ms, then retry").
DEFAULT_BUDGET_MS = 20_000
RETRY_PAUSE_MS = 100


class _GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]


def _guid(data1: int) -> tuple:
    # IUnknown {00000000-...} and IMessageFilter {00000016-...} share the
    # 0000-0000-C000-000000000046 tail.
    return (data1, 0, 0, (0xC0, 0, 0, 0, 0, 0, 0, 0x46))


_IID_IUNKNOWN = _guid(0x00000000)
_IID_IMESSAGEFILTER = _guid(0x00000016)


def _same_iid(ptr, iid: tuple) -> bool:
    g = ptr.contents
    return (g.Data1, g.Data2, g.Data3, tuple(g.Data4)) == iid


def retry_decision(reject_type: int, tick_count: int, budget_ms: int) -> int:
    """``RetryRejectedCall``'s answer: retry after a pause, or cancel."""
    if reject_type == _SERVERCALL_RETRYLATER and tick_count < budget_ms:
        return RETRY_PAUSE_MS
    return _CANCEL


# Keeps the vtable, the object and the callbacks alive for the process: COM
# holds raw pointers to them, and a collected callback would crash the thread.
_KEEPALIVE: list = []


def register(budget_ms: int = DEFAULT_BUDGET_MS) -> bool:
    """Register the filter on the calling (STA) thread; True when registered.

    Never raises: without the filter the backend still works, only without
    the automatic retry, so a failure is logged and reported as False.
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import WINFUNCTYPE

        hresult = ctypes.c_long
        dword = ctypes.c_uint32
        query_interface = WINFUNCTYPE(hresult, c_void_p, POINTER(_GUID), POINTER(c_void_p))
        add_ref = WINFUNCTYPE(c_ulong, c_void_p)
        release = WINFUNCTYPE(c_ulong, c_void_p)
        handle_incoming = WINFUNCTYPE(dword, c_void_p, dword, c_void_p, dword, c_void_p)
        retry_rejected = WINFUNCTYPE(dword, c_void_p, c_void_p, dword, dword)
        message_pending = WINFUNCTYPE(dword, c_void_p, c_void_p, dword, dword)

        class _VTable(Structure):
            _fields_ = [
                ("QueryInterface", query_interface),
                ("AddRef", add_ref),
                ("Release", release),
                ("HandleInComingCall", handle_incoming),
                ("RetryRejectedCall", retry_rejected),
                ("MessagePending", message_pending),
            ]

        class _Filter(Structure):
            _fields_ = [("lpVtbl", POINTER(_VTable))]

        def _qi(this, riid, ppv):
            if _same_iid(riid, _IID_IUNKNOWN) or _same_iid(riid, _IID_IMESSAGEFILTER):
                ppv[0] = this
                return _S_OK
            ppv[0] = None
            return _E_NOINTERFACE

        def _retry(this, htask, tick_count, reject_type):
            answer = retry_decision(reject_type, tick_count, budget_ms)
            if answer == _CANCEL and reject_type == _SERVERCALL_RETRYLATER:
                log.warning("AutoCAD refused a call for %d ms; giving up on it", tick_count)
            return answer

        vtable = _VTable(
            query_interface(_qi),
            add_ref(lambda this: 1),  # a static object: reference counts are moot
            release(lambda this: 1),
            handle_incoming(lambda this, call_type, htask, tick, info: _SERVERCALL_ISHANDLED),
            retry_rejected(_retry),
            message_pending(lambda this, htask, tick, pending: _PENDINGMSG_WAITDEFPROCESS),
        )
        obj = _Filter(ctypes.pointer(vtable))
        previous = c_void_p()
        hr = ctypes.oledll.ole32.CoRegisterMessageFilter(byref(obj), byref(previous))
        _KEEPALIVE.append((vtable, obj))
        log.debug("COM message filter registered (hr=%s, budget %d ms)", hr, budget_ms)
        return True
    except Exception as exc:  # pragma: no cover - depends on the host's ole32
        log.warning("COM message filter not registered (%s); refused calls fail at once", exc)
        return False
