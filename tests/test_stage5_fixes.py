# tests/test_stage5_fixes.py
"""Stage 5 of the token-economy roadmap: the gaps the autocad-spds skill worked around.

Each fix removes calls a client used to spend on a workaround:
* `entity_create_text` takes `style`, `align`, `width_factor` (the skill drew
  every centred label with entmake);
* every `dimension_*` takes `dimstyle` (AutoCAD dimensions with the current
  *saved* style; the skill ran `-DIMSTYLE _R` before each scale);
* `viewport_create(remove_others=)`, and live layout switches no longer let
  AutoCAD add a viewport of its own (LAYOUTCREATEVIEWPORT held at 0);
* `entity_edit_geometry` edits LWPOLYLINE vertices and the closed flag (the
  skill used entmod on group 10).
"""

from __future__ import annotations

import sys

import ezdxf
import pytest
from fastmcp import Client

import server
from backends import com_backend as com
from backends.contracts.entity_creation import TEXT_ALIGN_CODES, text_align_code
from backends.contracts.entity_query import edited_polyline_vertices
from backends.ezdxf_backend import EzdxfBackend

pytestmark = pytest.mark.asyncio


async def _backend():
    b = EzdxfBackend()
    await b.drawing_new()
    return b


def _ent(b, handle):
    return b._doc.entitydb[handle]


# ── text ────────────────────────────────────────────────────────────────────


def test_every_autocad_justification_is_known_and_others_refused():
    assert set(TEXT_ALIGN_CODES) == {
        "L", "C", "R", "M", "BL", "BC", "BR", "ML", "MC", "MR", "TL", "TC", "TR",
    }  # fmt: skip
    assert text_align_code(" mc ") == "MC"
    with pytest.raises(ValueError, match="TL, TC, TR"):
        text_align_code("centre")


async def test_text_takes_a_style_a_justification_and_a_width_factor():
    b = await _backend()
    await b.textstyle_create("GOST", "isocpeur.ttf")
    info = await b.entity_create_text("А-А", 100, 50, 5, style="gost", align="MC", width_factor=0.8)
    ent = _ent(b, info.handle)
    assert ent.dxf.style == "GOST"
    assert (ent.dxf.halign, ent.dxf.valign) == (1, 2)
    assert tuple(ent.dxf.align_point)[:2] == (100.0, 50.0)
    assert ent.dxf.width == 0.8
    with pytest.raises(ValueError, match="styles: GOST, Standard"):
        await b.entity_create_text("x", 0, 0, style="NOPE")


def test_the_live_alignment_table_covers_every_code():
    assert set(com._ACTIVEX_TEXT_ALIGNMENT) == set(TEXT_ALIGN_CODES)


# ── dimension style ─────────────────────────────────────────────────────────


async def test_a_dimension_draws_with_the_named_style_and_puts_the_current_one_back():
    b = await _backend()
    await b.dimstyle_create("СПДС_1-100", {"DIMTXT": 2.5})
    async with server._DimstyleFor(b, "спдс_1-100"):
        info = await b.dimension_linear(0, 0, 100, 0, 50, -10)
    assert _ent(b, info.handle).dxf.dimstyle == "СПДС_1-100"
    current = next(r["name"] for r in await b.dimstyle_list() if r["current"])
    assert current == "Standard"


async def test_an_unknown_dimstyle_is_refused_before_anything_is_drawn():
    async with Client(server.mcp) as client:
        await client.call_tool("drawing_new", {})
        out = await client.call_tool(
            "dimension_aligned",
            {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "dim_x": 0, "dim_y": 2, "dimstyle": "nope"},
            raise_on_error=False,
        )
        listed = await client.call_tool("entity_list", {})
    assert out.is_error and "styles: Standard" in out.content[0].text
    assert "DIMENSION" not in str(listed.structured_content)


# ── viewports ───────────────────────────────────────────────────────────────


async def test_remove_others_clears_every_other_viewport_but_the_sheet_itself():
    b = await _backend()
    await b.layout_create("A3")
    first = await b.viewport_create("A3", 100, 100, 50, 50, 0, 0)
    second = await b.viewport_create("A3", 200, 100, 80, 80, 0, 0, remove_others=True)
    assert second["removed"] == [first["handle"]]
    layout = b._doc.layouts.get("A3")
    left = [v.dxf.handle for v in layout.query("VIEWPORT") if v.dxf.get("id") != 1]
    assert left == [second["handle"]]


async def test_a_viewport_frame_can_go_on_a_layer_that_never_plots():
    b = await _backend()
    await b.layout_create("A3")
    out = await b.viewport_create("A3", 100, 100, 50, 50, 0, 0, layer="Defpoints")
    vp = next(
        v for v in b._doc.layouts.get("A3").query("VIEWPORT") if v.dxf.handle == out["handle"]
    )
    assert vp.dxf.layer == "Defpoints" and b._doc.layers.has_entry("Defpoints")


class _Layout:
    Name = "Лист1"


class _Doc:
    def __init__(self, reject_sets: int = 0, reject_reads: int = 0):
        self.vars = {"LAYOUTCREATEVIEWPORT": 1}
        self.seen = []
        self.reject_sets = reject_sets  # SetVariable calls to refuse, after the first
        self.reject_reads = reject_reads

    def GetVariable(self, name):
        return self.vars[name]

    def SetVariable(self, name, value):
        if self.seen and self.reject_sets:
            self.reject_sets -= 1
            raise _Rejected(com._RPC_E_CALL_REJECTED, "Call was rejected by callee.")
        self.vars[name] = value
        self.seen.append((name, value))

    @property
    def ActiveLayout(self):
        if self.reject_reads:
            self.reject_reads -= 1
            raise AttributeError("<unknown>.ActiveLayout")
        return _Layout()


def test_a_live_layout_switch_holds_layoutcreateviewport_at_zero_and_restores_it():
    doc = _Doc()
    with com._no_auto_viewport(doc):
        assert doc.vars["LAYOUTCREATEVIEWPORT"] == 0
    assert doc.vars["LAYOUTCREATEVIEWPORT"] == 1
    with pytest.raises(RuntimeError), com._no_auto_viewport(doc):
        raise RuntimeError("the switch failed")
    assert doc.vars["LAYOUTCREATEVIEWPORT"] == 1, "restored on failure too"


def test_a_rejected_restore_of_layoutcreateviewport_is_retried(monkeypatch):
    # Measured on AutoCAD 2024 (24.3): a fresh layout's first activation keeps
    # the seat busy, and the restore used to fail silently, leaving it at 0.
    monkeypatch.setattr(com, "_COM_ERROR", (_Rejected,))
    monkeypatch.setattr(com, "_SWITCH_SETTLE_FIRST_PAUSE_S", 0.01)
    doc = _Doc(reject_sets=2, reject_reads=2)
    with com._no_auto_viewport(doc):
        pass
    assert doc.vars["LAYOUTCREATEVIEWPORT"] == 1
    assert doc.reject_reads == 0, "the switch waited until the seat answered a read"


class _BusyAfterCommand:
    """CMDACTIVE refused ``busy`` times, then 0; Linetypes.Load refused once."""

    def __init__(self, busy):
        self.busy, self.loaded = busy, []
        self.Linetypes = self

    def GetVariable(self, name):
        assert name == "CMDACTIVE"
        if self.busy:
            self.busy -= 1
            raise _Rejected(com._RPC_E_CALL_REJECTED, "Call was rejected by callee.")
        return 0

    def Load(self, name, path):
        if not self.loaded:
            self.loaded.append(None)
            raise _Rejected(com._RPC_E_CALL_REJECTED, "Call was rejected by callee.")
        self.loaded.append(name)


def test_the_seat_is_settled_after_a_command_and_a_refused_load_is_retried(monkeypatch):
    monkeypatch.setattr(com, "_COM_ERROR", (_Rejected,))
    monkeypatch.setattr(com, "_SWITCH_SETTLE_FIRST_PAUSE_S", 0.01)
    doc = _BusyAfterCommand(busy=3)
    assert com._settle_after_command(doc, budget_s=1.0, poll_s=0.01) is True
    assert doc.busy == 0
    assert com._settle_after_command(_BusyAfterCommand(busy=10**6), budget_s=0.05) is False
    com._load_linetype(doc, "GOST_AXIS", "x.lin")
    assert doc.loaded == [None, "GOST_AXIS"]


def test_the_first_read_of_a_tool_waits_out_a_busy_seat(monkeypatch):
    app = _FakeApp()
    app.Documents = _LookupRejectedDocs([_FakeDoc(app, "a.dwg", False)])
    app.Documents.refusals = 2
    monkeypatch.setattr(com, "_acad_app", lambda: app)
    monkeypatch.setattr(com, "_SWITCH_SETTLE_FIRST_PAUSE_S", 0.01)
    assert com._acad_doc().Name == "a.dwg"
    app.Documents = _FakeDocs([])
    with pytest.raises(RuntimeError, match="No drawing is open"):
        com._acad_doc()


def test_a_rejected_member_lookup_is_waited_out_but_a_real_missing_member_is_not():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise AttributeError("<unknown>.Count")  # pywin32's rejected GetIDsOfNames
        return 7

    assert com._wait_out_rejected_call(flaky, budget_s=1.0, first_pause_s=0.01) == 7
    with pytest.raises(AttributeError):
        com._wait_out_rejected_call(
            lambda: (_ for _ in ()).throw(AttributeError("'dict' has no Count")),
            budget_s=1.0,
            first_pause_s=0.01,
        )


# ── polyline vertices ───────────────────────────────────────────────────────


def test_the_vertex_rules():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert edited_polyline_vertices(pts, None, -1, None, -5) == [(0, 0), (10, 0), (10, -5)]
    assert edited_polyline_vertices(pts, [[1, 1], [2, 2]], None, None, None) == [(1, 1), (2, 2)]
    assert edited_polyline_vertices(pts, None, None, None, None) is None
    for bad in ((None, 3, 1.0, None), ([[0, 0]], None, None, None), (None, 0, None, None)):
        with pytest.raises(ValueError):
            edited_polyline_vertices(pts, *bad)


async def test_an_axis_is_extended_by_moving_its_last_vertex():
    b = await _backend()
    info = await b.entity_create_polyline([[0, 0], [0, 100]])
    await b.entity_edit_geometry(info.handle, vertex_index=-1, vy=-50)
    ent = _ent(b, info.handle)
    assert [tuple(p) for p in ent.get_points("xy")] == [(0, 0), (0, -50)]


async def test_a_contour_drawn_closed_is_flagged_closed_and_keeps_its_bulges():
    b = await _backend()
    info = await b.entity_create_polyline([[0, 0], [10, 0], [10, 10], [0, 10]])
    ent = _ent(b, info.handle)
    ent.set_points([(0, 0, 0, 0, 0.5), (10, 0), (10, 10), (0, 10)], format="xyseb")
    await b.entity_edit_geometry(info.handle, vertex_index=2, vx=12, closed=True)
    ent = _ent(b, info.handle)
    assert ent.closed
    assert [round(p[4], 3) for p in ent.get_points("xyseb")] == [0.5, 0, 0, 0]
    assert tuple(ent.get_points("xy")[2]) == (12, 10)


async def test_a_mirrored_polyline_is_edited_in_wcs():
    b = await _backend()
    info = await b.entity_create_polyline([[0, 0], [10, 0]])
    ent = _ent(b, info.handle)
    ent.dxf.extrusion = (0, 0, -1)
    ent.set_points([(0, 0), (-10, 0)], format="xy")  # OCS of WCS (0,0)->(10,0)
    await b.entity_edit_geometry(info.handle, vertex_index=1, vx=20)
    assert [tuple(p) for p in _ent(b, info.handle).get_points("xy")] == [(0, 0), (-20, 0)]


async def test_polyline_arguments_on_a_line_are_refused():
    b = await _backend()
    info = await b.entity_create_line(0, 0, 1, 1)
    with pytest.raises(RuntimeError, match="LWPOLYLINE, not a LINE"):
        await b.entity_edit_geometry(info.handle, closed=True)


def test_ezdxf_is_importable():  # keeps the module honest about its fixtures
    assert ezdxf.__version__


# ── drawing_close: a rejected Close that closed ─────────────────────────────


class _Rejected(Exception):
    pass


class _FakeDocs:
    def __init__(self, docs):
        self.docs = docs

    @property
    def Count(self):
        return len(self.docs)

    def Item(self, i):
        return self.docs[i]


class _FakeDoc:
    def __init__(self, app, name, closes: bool):
        self.app, self.Name, self.FullName, self.Saved = app, name, f"C:/w/{name}", True
        self.closes = closes

    def Close(self, save):
        if self.closes:
            self.app.Documents.docs.remove(self)
        raise _Rejected(com._RPC_E_CALL_REJECTED, "Call was rejected by callee.")


class _FakeApp:
    def __init__(self):
        self.Documents = _FakeDocs([])

    @property
    def ActiveDocument(self):
        return self.Documents.docs[0]


class _CloseBackend:
    _REJECTED_RETRY_BUDGET_S = 0.2
    _REJECTED_RETRY_FIRST_PAUSE_S = 0.01
    _com_documents = staticmethod(com.ComBackend._com_documents)
    _wait_out_rejected_call = com.ComBackend._wait_out_rejected_call

    async def _run(self, func, *args):
        return func(*args)

    async def _ensure_document_state(self):
        return None


@pytest.mark.parametrize("closes", [True, False])
async def test_a_rejected_close_is_judged_by_the_document_list(monkeypatch, closes):
    app = _FakeApp()
    target = _FakeDoc(app, "a.dwg", closes)
    app.Documents.docs = [target, _FakeDoc(app, "b.dwg", False)]
    monkeypatch.setattr(com, "_COM_ERROR", (_Rejected,))
    monkeypatch.setattr(com, "_acad_app", lambda: app)
    monkeypatch.setattr(com, "_acad_doc", lambda: target)
    call = com.ComBackend.document_close(_CloseBackend(), None, discard=True)
    if closes:
        out = await call
        assert out["closed"] == "a.dwg" and out["close_call_rejected_but_closed"] is True
        assert out["open_documents"] == 1
    else:
        with pytest.raises(_Rejected):
            await call


class _LookupRejectedDocs(_FakeDocs):
    """``Documents.Count`` refused once after the Close, as pywin32 reports it."""

    def __init__(self, docs):
        super().__init__(docs)
        self.refusals = 1

    @property
    def Count(self):
        if self.refusals:
            self.refusals -= 1
            raise AttributeError("<unknown>.Count")
        return len(self.docs)


class _LookupRejectedDoc(_FakeDoc):
    """The ``Close`` member lookup refused once: the Close never ran."""

    def __init__(self, app, name):
        super().__init__(app, name, closes=True)
        self.lookups_refused = 0

    def __getattribute__(self, attr):
        if attr == "Close" and object.__getattribute__(self, "lookups_refused") == 0:
            self.lookups_refused = 1
            raise AttributeError("<unknown>.Close")
        return object.__getattribute__(self, attr)

    def Close(self, save):
        self.app.Documents.docs.remove(self)


async def test_a_rejected_lookup_after_close_does_not_fail_a_close_that_happened(monkeypatch):
    # Measured on AutoCAD 2024 (24.3): drawing_close failed with
    # "<unknown>.Count" although the document had closed.
    app = _FakeApp()
    target = _FakeDoc(app, "a.dwg", closes=True)
    target.Close = lambda save: app.Documents.docs.remove(target)
    app.Documents = _LookupRejectedDocs([target, _FakeDoc(app, "b.dwg", False)])
    monkeypatch.setattr(com, "_COM_ERROR", (_Rejected,))
    monkeypatch.setattr(com, "_acad_app", lambda: app)
    monkeypatch.setattr(com, "_acad_doc", lambda: target)
    out = await com.ComBackend.document_close(_CloseBackend(), None, discard=True)
    assert out["closed"] == "a.dwg" and out["open_documents"] == 1
    assert "close_call_rejected_but_closed" not in out


async def test_a_refused_close_lookup_is_retried_once_the_seat_answers(monkeypatch):
    app = _FakeApp()
    target = _LookupRejectedDoc(app, "a.dwg")
    app.Documents.docs = [target, _FakeDoc(app, "b.dwg", False)]
    monkeypatch.setattr(com, "_COM_ERROR", (_Rejected,))
    monkeypatch.setattr(com, "_acad_app", lambda: app)
    monkeypatch.setattr(com, "_acad_doc", lambda: target)
    out = await com.ComBackend.document_close(_CloseBackend(), None, discard=True)
    assert out["closed"] == "a.dwg" and out["open_documents"] == 1


# ── a missing folder inside an allowed root ─────────────────────────────────


def test_a_missing_folder_inside_an_allowed_root_is_created(monkeypatch, tmp_path):
    from fastmcp.exceptions import ToolError

    import config
    from security import validate_path

    root = tmp_path / "AutoCAD"
    root.mkdir()
    monkeypatch.setattr(config.settings, "allowed_paths", [root.resolve()])
    target = root / "КЖ" / "листы" / "a3.pdf"
    out = validate_path(str(target), allow_write=True)
    assert out.parent.is_dir(), "created, so drawing_export_pdf can write there"
    outside = tmp_path / "elsewhere" / "x.pdf"
    with pytest.raises(ToolError):
        validate_path(str(outside), allow_write=True)
    assert not outside.parent.exists()


# ── the COM message filter ──────────────────────────────────────────────────


def test_the_message_filter_retries_only_a_busy_callee_and_only_within_budget():
    from backends import com_message_filter as mf

    assert mf.retry_decision(mf._SERVERCALL_RETRYLATER, 0, 1000) == mf.RETRY_PAUSE_MS
    assert mf.retry_decision(mf._SERVERCALL_RETRYLATER, 1000, 1000) == mf._CANCEL
    assert mf.retry_decision(1, 0, 1000) == mf._CANCEL  # SERVERCALL_REJECTED: never


@pytest.mark.skipif(sys.platform != "win32", reason="CoRegisterMessageFilter is Windows COM")
def test_the_message_filter_registers_on_an_sta_thread():
    import threading

    pythoncom = pytest.importorskip("pythoncom")
    from backends import com_message_filter as mf

    out = []

    def worker():
        pythoncom.CoInitialize()
        try:
            out.append(mf.register(budget_ms=500))
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)
    assert out == [True]


# ── viewports in a live sheet ───────────────────────────────────────────────


class _Vp:
    ObjectName = "AcDbViewport"

    def __init__(self, space, handle):
        self.space, self.Handle = space, handle

    def Delete(self):
        self.space.items.remove(self)


class _Space:
    def __init__(self, handles):
        self.items = [_Vp(self, h) for h in handles]

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i]


def test_remove_others_never_deletes_the_sheets_own_viewport():
    # An AcadPViewport has no `Number` (AttributeError on AutoCAD 2024), so the
    # sheet's viewport is told by order: the layout's first.
    class _D:
        PaperSpace = _Space(["80", "85", "8A"])

    assert com._delete_other_viewports(_D, "8A") == ["85"]
    assert [v.Handle for v in _D.PaperSpace.items] == ["80", "8A"]


def test_a_snapshot_viewport_off_screen_is_drawn():
    from backends.render import render_doc, wake_offscreen_viewports

    doc = ezdxf.new()
    doc.modelspace().add_circle((6000, 3000), 3000)
    layout = doc.layouts.new("Лист1")
    layout.page_setup(size=(420, 297), margins=(0, 0, 0, 0), units="mm")  # the sheet's own
    vp = layout.add_viewport(
        center=(200, 150), size=(300, 200), view_center_point=(6000, 3000), view_height=20000
    )
    vp.dxf.status = -1  # what AutoCAD exports for a layout not on screen
    blank = render_doc(doc, space="Лист1", width=300)["png"]
    assert wake_offscreen_viewports(doc) == 1
    drawn = render_doc(doc, space="Лист1", width=300)["png"]
    assert vp.dxf.status > 1
    assert drawn != blank


def test_a_locked_pdf_is_named_before_plotting(tmp_path):
    assert com._locked_output(str(tmp_path / "new.pdf")) is None
    free = tmp_path / "free.pdf"
    free.write_bytes(b"%PDF")
    assert com._locked_output(str(free)) is None
    if sys.platform != "win32":
        return
    win32file = pytest.importorskip("win32file")
    held = tmp_path / "held.pdf"
    held.write_bytes(b"%PDF")
    # share mode 0: what a viewer holding the file open amounts to
    handle = win32file.CreateFile(
        str(held), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None
    )
    try:
        assert "cannot be overwritten" in com._locked_output(str(held))
    finally:
        handle.Close()
