# tests/test_run_lisp_result.py
"""system_run_lisp reads the value of a *queued* form (v1.7).

`SendCommand` returns before AutoCAD evaluates the form: the command line runs
it once the application is idle. v1.6 polled CMDACTIVE (0 before the form had
even started) and read USERS1 immediately, so the autocad-spds skill saw every
value as "nil" / "OK" and wrote results into TEXT entities to read them back.
The backend now puts a sentinel into USERS1 and waits for the form to replace
it. These fakes evaluate the form late, on another thread, like AutoCAD does.
"""

from __future__ import annotations

import threading

import pytest

from backends import com_backend as com

pytestmark = pytest.mark.asyncio


class _LateDocument:
    """A document whose command line evaluates a form ``delay`` seconds later."""

    def __init__(self, outcome: str | None, delay: float = 0.15):
        self.vars = {"CMDACTIVE": 0, "OSMODE": 0, "SNAPMODE": 0, "CMDECHO": 0, "USERS1": ""}
        self.outcome = outcome
        self.delay = delay
        self.sent: list[str] = []
        self.ModelSpace = []

    def GetVariable(self, name):
        return self.vars[name]

    def SetVariable(self, name, value):
        self.vars[name] = value

    def SendCommand(self, payload):
        self.sent.append(payload)
        if self.outcome is None:
            return  # never evaluated

        def _evaluate():
            self.vars["USERS1"] = self.outcome

        threading.Timer(self.delay, _evaluate).start()


class _Backend:
    _safe_send_command = staticmethod(com.ComBackend._safe_send_command)

    async def _run(self, func, *args):
        return func(*args)


async def _run_lisp(monkeypatch, doc, expression="(+ 40 2)"):
    monkeypatch.setattr(com, "_acad_doc", lambda: doc)
    return await com.ComBackend.system_run_lisp(_Backend(), expression)


async def test_the_value_of_a_late_form_is_read(monkeypatch):
    doc = _LateDocument("42")
    out = await _run_lisp(monkeypatch, doc)
    assert out == {"ok": True, "expression": "(+ 40 2)", "result": "42"}
    assert "vl-catch-all-apply" in doc.sent[0] and "(+ 40 2)" in doc.sent[0]


async def test_an_autolisp_error_comes_back_as_an_error(monkeypatch):
    doc = _LateDocument(com._LISP_ERROR_MARK + "bad argument type: numberp: nil")
    out = await _run_lisp(monkeypatch, doc, "(+ nil 1)")
    assert out["ok"] is False
    assert out["error"] == "bad argument type: numberp: nil"


async def test_an_empty_value_reads_as_nil(monkeypatch):
    out = await _run_lisp(monkeypatch, _LateDocument(""))
    assert out["result"] == "nil"


async def test_a_form_that_never_runs_is_reported_pending_not_nil(monkeypatch):
    monkeypatch.setattr(com, "LISP_RESULT_TIMEOUT_S", 0.3)
    out = await _run_lisp(monkeypatch, _LateDocument(None))
    assert out["pending"] is True and out["result"] is None
    assert "system_status" in out["message"]
