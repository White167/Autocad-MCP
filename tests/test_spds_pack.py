# tests/test_spds_pack.py
"""The `spds` pack (SECTION 28): ГОСТ Р 21.101-2020 sheets and marks in one call each.

The values come from engineering/spds/standards.py, transcribed from the owner's
verified `autocad-spds` library; these tests pin the geometry and the rules
(form 3 lines, frame margins, level formatting, axis lettering) and drive the
six tools end to end on the headless engine.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client

import config
import server
from engineering.spds.draw import Seg, Txt, text_width
from engineering.spds.marks import (
    axis_grid_marks,
    axis_grid_prims,
    axis_letters,
    axis_prims,
    elevation_prims,
    format_elevation,
    leader_prims,
)
from engineering.spds.sheet import normalise_fields, sheet_prims
from engineering.spds.standards import (
    AXIS_LETTERS,
    FORBIDDEN_AXIS_LETTERS,
    FORM3,
    dimstyle_values,
    sheet_size,
)
from engineering.standards.dimstyles import validate_overrides

pytestmark = pytest.mark.asyncio


# ── standards ───────────────────────────────────────────────────────────────


def test_only_the_transcribed_formats_exist():
    assert sheet_size("А3")[:2] == (420.0, 297.0), "a Cyrillic А is accepted"
    assert sheet_size("A4")[:2] == (210.0, 297.0)
    assert sheet_size("A3", "portrait")[:2] == (297.0, 420.0)
    for fmt, orient in (("A4", "landscape"), ("A2", None), ("A1", "landscape")):
        with pytest.raises(ValueError, match="not transcribed"):
            sheet_size(fmt, orient)


def test_form_3_is_185_by_55_and_fits_an_a4_frame_exactly():
    assert (FORM3["width"], FORM3["height"]) == (185.0, 55.0)
    # A4: 210 - 20 (filing) - 5 = 185, the whole frame width
    sheet = sheet_prims("A4")
    fl, _fb, fr, _ft = sheet["frame"]
    assert fr - fl == FORM3["width"]
    assert sheet["stamp_origin"] == (fl, 5.0)


def test_the_dimension_style_passes_the_whitelist():
    values = dimstyle_values(100)
    assert validate_overrides(values)["DIMSCALE"] == 100.0
    assert values["DIMBLK"] == "_ARCHTICK" and values["DIMDSEP"] == ","


def test_the_axis_alphabet_has_no_forbidden_letter():
    assert not set(AXIS_LETTERS) & FORBIDDEN_AXIS_LETTERS
    assert AXIS_LETTERS[:9] == ("А", "Б", "В", "Г", "Д", "Е", "Ж", "И", "К")
    assert axis_letters(len(AXIS_LETTERS) + 2)[-2:] == ["АА", "ББ"]


# ── the sheet ───────────────────────────────────────────────────────────────


def test_the_a3_sheet_has_its_frame_its_stamp_and_its_label():
    sheet = sheet_prims(
        "A3",
        fields={"code": "КП-КЖ", 7: "1", "sheets": "3"},
        signatures=[["Разраб.", "Иванов", "10.26"]],
    )
    assert sheet["frame"] == (20.0, 5.0, 415.0, 292.0)
    assert sheet["stamp_origin"] == (230.0, 5.0)
    segs = [p for p in sheet["prims"] if isinstance(p, Seg)]
    thick = [s for s in segs if s.layer == "СПДС_Рамка"]
    thin = [s for s in segs if s.layer == "СПДС_Рамка_тонкие"]
    assert len(thick) == 4 + len(FORM3["thick"])
    assert len(thin) == 4 + len(FORM3["thin"])
    texts = {p.text: p for p in sheet["prims"] if isinstance(p, Txt)}
    assert texts["КП-КЖ"].height == 5.0 and texts["КП-КЖ"].align == "MC"
    assert texts["КП-КЖ"].at == (230 + 125, 5 + 50)
    assert texts["Формат А3"].at == (390.0, 2.5)
    assert texts["Иванов"].align == "ML"


def test_a_long_name_is_compressed_then_wrapped():
    long = "Многоэтажный жилой дом с подземной автостоянкой в г. Чебоксары"
    sheet = sheet_prims("A3", fields={"project": long})
    lines = [p for p in sheet["prims"] if isinstance(p, Txt) and p.height == 3.5]
    assert len(lines) == 2, "too wide even at 0.8: split at the middle space"
    assert " ".join(p.text for p in lines) == long
    for p in lines:
        assert text_width(p.text, p.height, p.width_factor) <= 115.0 + 1e-6


def test_fields_by_name_or_number_and_unknown_ones_refused():
    assert normalise_fields({"code": "X", "2": "Y", 9: "Z", "stage": ""}) == {
        1: "X",
        2: "Y",
        9: "Z",
    }
    with pytest.raises(ValueError, match="graph of form 3"):
        normalise_fields({"designer": "x"})
    with pytest.raises(ValueError, match="no cell"):
        normalise_fields({5: "x"})


def test_the_margin_graphs_are_optional():
    plain = sheet_prims("A3")
    extra = sheet_prims("A3", extra_columns=True)
    rotated = [p for p in extra["prims"] if isinstance(p, Txt) and p.rotation == 90.0]
    assert [p.text for p in rotated] == ["Инв. № подл.", "Подп. и дата", "Взам. инв. №"]
    assert len(extra["prims"]) == len(plain["prims"]) + 6 + 3


# ── marks ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "decimals", "signed", "text"),
    [
        (0, 3, True, "0,000"),
        (-0.0001, 3, True, "0,000"),
        (3.3, 3, True, "+3,300"),
        (-1.65, 3, True, "-1,650"),
        (160.65, 2, False, "160,65"),
        (-2.5, 2, False, "-2,50"),
    ],
)
def test_levels_are_metres_with_a_comma_and_the_zero_unsigned(value, decimals, signed, text):
    assert format_elevation(value, decimals, signed) == text


def test_the_section_level_sign_is_two_strokes_a_stem_and_a_shelf():
    prims = elevation_prims((0, 0), "+3,300")
    segs = [p for p in prims if isinstance(p, Seg)]
    assert segs[0] == Seg((0, 0), (-2.1, 2.1), "Отметки")
    assert segs[1] == Seg((0, 0), (2.1, 2.1), "Отметки")
    assert segs[2] == Seg((0, 0), (0, 6.0), "Отметки")
    assert segs[3].p1 == (0, 6.0) and segs[3].p2[0] > 0
    left = elevation_prims((0, 0), "+3,300", direction=-1)
    assert [p for p in left if isinstance(p, Seg)][3].p2[0] < 0


def test_a_plan_level_is_boxed():
    prims = elevation_prims((10, 10), "0,000", kind="plan")
    assert prims[0].closed and prims[1].align == "MC"


def test_axis_digits_go_to_the_side_with_more_axes():
    assert axis_grid_marks(5, 3) == (["1", "2", "3", "4", "5"], ["А", "Б", "В"], "vertical")
    assert axis_grid_marks(2, 4) == (["А", "Б"], ["1", "2", "3", "4"], "horizontal")
    assert axis_grid_marks(3, 3)[2] == "vertical"


def test_an_axis_mark_circle_sits_on_the_line_extension():
    prims = axis_prims((0, 0), (0, 1000), "1", scale=100)
    circle = prims[1]
    assert circle.center == (0.0, -4.0) and circle.radius == 4.0
    with pytest.raises(ValueError, match="6-12 mm"):
        axis_prims((0, 0), (0, 10), "1", diameter=14)


def test_the_grid_runs_past_the_outer_axes():
    grid = axis_grid_prims([0, 6000], [0, 3000, 6000], overhang=10, scale=100)
    axes = [p for p in grid["prims"] if isinstance(p, Seg)]
    vertical = axes[0]
    assert vertical.p1 == (0.0, -10.0) and vertical.p2 == (0.0, 70.0)
    assert grid["horizontal"] == {"1": 0.0, "2": 3000.0, "3": 6000.0}
    with pytest.raises(ValueError, match="share a position"):
        axis_grid_prims([0, 0], [0])


def test_a_leader_has_its_shelf_text_and_end():
    prims = leader_prims([(0, 0), (10, 10)], "Бетон В25", text_below="F150", end="arrow")
    kinds = [type(p).__name__ for p in prims]
    assert kinds == ["Poly", "Seg", "Txt", "Txt", "Fill"]
    shelf = prims[1]
    assert shelf.p1 == (10, 10) and shelf.p2[0] > 10, "the shelf runs away from the leader"
    with pytest.raises(ValueError, match="at least two"):
        leader_prims([(0, 0)], "x")


# ── the tools ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    async with Client(server.mcp) as c:
        await c.call_tool("drawing_new", {"bootstrap": False})
        yield c


async def test_setup_is_idempotent(client):
    first = (await client.call_tool("spds_setup", {"scales": [100, 50]})).structured_content
    again = (await client.call_tool("spds_setup", {"scales": [100]})).structured_content
    assert set(first["linetypes_created"]) == {"GOST_DASH", "GOST_AXIS", "GOST_2DOT"}
    assert first["dimstyles"] == {"СПДС_1-50": "created", "СПДС_1-100": "created"}
    assert again["layers_created"] == [] and again["dimstyles"] == {"СПДС_1-100": "present"}
    assert again["text_style"] == "present"


async def test_a_sheet_on_a_layout_and_marks_on_the_model(client):
    await client.call_tool("layout_create", {"name": "Лист1"})
    sheet = (
        await client.call_tool(
            "spds_sheet",
            {"format": "A3", "layout": "Лист1", "stamp": {"code": "КП-КЖ"}},
        )
    ).structured_content
    assert sheet["layout"] == "Лист1" and sheet["frame"] == [20.0, 5.0, 415.0, 292.0]
    grid = (
        await client.call_tool("spds_axis_grid", {"xs": [0, 6000], "ys": [0], "scale": 100})
    ).structured_content
    assert grid["horizontal"] == {"А": 0.0} and grid["entities"] == 3 * 3
    level = (
        await client.call_tool("spds_elevation", {"x": 0, "y": 0, "value": 0, "scale": 100})
    ).structured_content
    assert level["text"] == "0,000"
    digest = (await client.call_tool("drawing_digest", {})).content[0].text
    assert "open_ends" not in digest, "axes and level marks are not contours"


async def test_the_tools_refuse_before_drawing(client):
    bad = [
        ("spds_sheet", {"format": "A1"}),
        ("spds_axis", {"x1": 0, "y1": 0, "x2": 0, "y2": 1, "mark": "З"}),
        ("spds_elevation", {"x": 0, "y": 0}),
        ("spds_elevation", {"x": 0, "y": 0, "value": 1, "kind": "facade"}),
        ("spds_leader", {"points": [[0, 0], [1, 1]], "text": "x", "end": "star"}),
    ]
    for tool, args in bad:
        out = await client.call_tool(tool, args, raise_on_error=False)
        assert out.is_error, tool
    listed = (await client.call_tool("entity_list", {})).structured_content
    assert not (listed or {}).get("result"), "nothing was drawn"


async def test_the_spds_preset_lists_the_pack(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    try:
        async with Client(server.mcp) as c:
            names = {t.name for t in await c.list_tools()}
    finally:
        monkeypatch.setattr(config.settings, "tool_config", "")
        await server._apply_tool_profile("full")
    assert set(server.PACK_TOOL_NAMES["spds"]) <= names
