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


class _Doc:
    def __init__(self):
        self.vars = {"LAYOUTCREATEVIEWPORT": 1}
        self.seen = []

    def GetVariable(self, name):
        return self.vars[name]

    def SetVariable(self, name, value):
        self.vars[name] = value
        self.seen.append((name, value))


def test_a_live_layout_switch_holds_layoutcreateviewport_at_zero_and_restores_it():
    doc = _Doc()
    with com._no_auto_viewport(doc):
        assert doc.vars["LAYOUTCREATEVIEWPORT"] == 0
    assert doc.vars["LAYOUTCREATEVIEWPORT"] == 1
    with pytest.raises(RuntimeError), com._no_auto_viewport(doc):
        raise RuntimeError("the switch failed")
    assert doc.vars["LAYOUTCREATEVIEWPORT"] == 1, "restored on failure too"


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


# ── a missing folder inside an allowed root ─────────────────────────────────


def test_a_missing_folder_inside_an_allowed_root_is_created(monkeypatch, tmp_path):
    import config
    from fastmcp.exceptions import ToolError

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
