# tests/test_digest_checkpoint.py
"""Stage 2 of the token-economy roadmap: see what edits did without a screenshot.

`drawing_digest` - counts, extents and warnings in a few hundred characters;
`drawing_checkpoint` / `drawing_diff_since` - only what changed, one line each;
`responses = "compact"` - a write tool answers with its handle.
"""

from __future__ import annotations

import ezdxf
import pytest
import pytest_asyncio
from fastmcp import Client

import config
import server
from discovery.responses import compact_value, is_entity_record
from engineering.checkpoint import CheckpointStore, compact_diff, render_diff_text
from engineering.digest import digest, render_text
from engineering.understand.snapshot import records_from_doc

pytestmark = pytest.mark.asyncio


def _scene():
    """A sheet with one of every problem the digest reports, and their handles."""
    doc = ezdxf.new()
    doc.layers.add("GEOMETRY")
    doc.layers.add("TEXT")
    doc.layers.add("CONSTRUCTION")
    msp = doc.modelspace()
    h = {}
    h["frame"] = msp.add_lwpolyline(
        [(0, 0), (420, 0), (420, 297), (0, 297)], close=True, dxfattribs={"layer": "GEOMETRY"}
    ).dxf.handle
    # three sides of a square: the two free ends are open
    msp.add_line((10, 10), (50, 10), dxfattribs={"layer": "GEOMETRY"})
    h["open_a"] = msp.add_line((50, 10), (50, 50), dxfattribs={"layer": "GEOMETRY"}).dxf.handle
    msp.add_line((50, 50), (10, 50), dxfattribs={"layer": "GEOMETRY"})
    h["not_closed"] = msp.add_lwpolyline(
        [(100, 100), (150, 100), (150, 150), (100, 150), (100, 100)],
        dxfattribs={"layer": "GEOMETRY"},
    ).dxf.handle
    msp.add_circle((200, 200), 10, dxfattribs={"layer": "GEOMETRY"})
    h["duplicate"] = msp.add_circle((200, 200), 10, dxfattribs={"layer": "GEOMETRY"}).dxf.handle
    h["outside"] = msp.add_circle((500, 100), 5).dxf.handle  # layer 0, off the sheet
    msp.add_text("Note", dxfattribs={"layer": "TEXT", "height": 3.5}).set_placement((20, 250))
    odd = msp.add_text("Odd", dxfattribs={"layer": "TEXT", "height": 3.0})
    odd.set_placement((20, 240))
    h["bad_text"] = odd.dxf.handle
    h["construction"] = msp.add_line(
        (300, 20), (310, 30), dxfattribs={"layer": "CONSTRUCTION"}
    ).dxf.handle
    return doc, h


# ── digest ──────────────────────────────────────────────────────────────────


def test_the_digest_finds_every_planted_problem():
    doc, h = _scene()
    d = digest(records_from_doc(doc, source="t"), text_heights=[2.5, 3.5, 5, 7])
    checks = d["checks"]
    assert checks["outside_frame"]["handles"] == [h["outside"]]
    assert checks["layer_0"]["handles"] == [h["outside"]]
    assert checks["duplicate"]["handles"] == [h["duplicate"]]
    assert checks["not_closed"]["handles"] == [h["not_closed"]]
    assert checks["text_height"]["handles"] == [h["bad_text"]]
    assert checks["construction_left"]["handles"] == [h["construction"]]
    assert checks["open_ends"]["count"] == 2, "the two free ends of the three-sided square"
    assert d["frame"]["handle"] == h["frame"] and d["frame"]["source"] == "largest_rectangle"
    assert "CONSTRUCTION" not in (d["contour_layers"] or {}).get("layers", [])
    assert d["ok"] is False


def test_a_clean_drawing_says_so():
    doc = ezdxf.new()
    doc.layers.add("GEOMETRY")
    doc.modelspace().add_lwpolyline(
        [(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "GEOMETRY"}
    )
    d = digest(records_from_doc(doc, source="t"))
    assert d["ok"] and d["checks"] == {}
    assert render_text(d).endswith("OK no warnings")


def test_the_text_form_is_short_and_keeps_its_budget():
    doc, _h = _scene()
    msp = doc.modelspace()
    for i in range(60):  # many layers: the list is what gets cut
        doc.layers.add(f"L{i:02d}")
        msp.add_point((i, 0), dxfattribs={"layer": f"L{i:02d}"})
    d = digest(records_from_doc(doc, source="t"))
    text = render_text(d, 600)
    assert len(text) <= 600
    assert "more layers" in text
    assert "WARN outside_frame" in text, "warnings survive the cut"


def test_window_and_layer_filters_and_an_unknown_layout():
    doc, h = _scene()
    snap = records_from_doc(doc, source="t")
    d = digest(snap, window=[480, 80, 520, 120])
    assert d["entities"] == 1 and list(d["by_layer"]) == ["0"]
    d = digest(snap, layers=["text"])
    assert set(d["by_layer"]) == {"TEXT"}
    with pytest.raises(ValueError, match="no layout named"):
        digest(snap, space="Sheet9")


# ── checkpoint / diff ───────────────────────────────────────────────────────


def test_the_diff_names_added_changed_and_removed_in_one_line_each():
    doc = ezdxf.new()
    msp = doc.modelspace()
    keep = msp.add_circle((0, 0), 10)
    gone = msp.add_line((0, 0), (5, 5))
    gone_handle = gone.dxf.handle
    before = records_from_doc(doc, source="t")
    keep.dxf.radius = 12
    msp.delete_entity(gone)
    added = msp.add_text("Новый", dxfattribs={"height": 2.5})
    d = compact_diff(before, records_from_doc(doc, source="t"))
    assert d["summary"] == {"before": 2, "after": 2, "added": 1, "changed": 1, "removed": 1}
    assert d["added"][0]["handle"] == added.dxf.handle and '"Новый"' in d["added"][0]["what"]
    assert d["changed"][0]["what"].endswith("radius 10->12")
    assert d["removed"][0]["handle"] == gone_handle
    text = render_diff_text(d, name="default", age_s=3)
    assert text.splitlines()[0].startswith('SINCE "default", 3s ago: +1 ~1 -1')


def test_the_store_is_bounded():
    store = CheckpointStore()
    doc = ezdxf.new()
    snap = records_from_doc(doc, source="t")
    for i in range(20):
        store.put(f"cp{i}", snap)
    assert len(store.names()) == 16 and store.get("cp0") is None and store.get("cp19")


async def test_checkpoint_and_diff_since_through_the_server():
    async with Client(server.mcp) as client:
        await client.call_tool("drawing_new", {})
        first = (await client.call_tool("drawing_diff_since", {"name": "t"})).content[0].text
        circle = await client.call_tool("entity_create_circle", {"cx": 5, "cy": 5, "radius": 2})
        handle = circle.structured_content["handle"]
        diff = await client.call_tool("drawing_diff_since", {"name": "t"})
        await client.call_tool("entity_move", {"handle": handle, "dx": 1, "dy": 0})
        moved = (await client.call_tool("drawing_diff_since", {"name": "t"})).content[0].text
        again = (await client.call_tool("drawing_diff_since", {"name": "t"})).content[0].text
        await client.call_tool("drawing_new", {})
        dropped = (await client.call_tool("drawing_diff_since", {"name": "t"})).content[0].text
    assert first.startswith('no checkpoint "t" yet')
    assert diff.structured_content is None, "text, not text plus a structured twin"
    assert f"+ {handle} CIRCLE" in diff.content[0].text
    assert f"~ {handle} CIRCLE" in moved and "moved 1,0" in moved
    assert "no changes" in again, "advance re-armed the checkpoint"
    assert dropped.startswith('no checkpoint "t" yet'), "drawing_new drops checkpoints"


async def test_drawing_digest_through_the_server():
    async with Client(server.mcp) as client:
        await client.call_tool("drawing_new", {})
        await client.call_tool(
            "entity_create_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 50, "layer": "GEOMETRY"}
        )
        await client.call_tool("entity_create_circle", {"cx": 200, "cy": 10, "radius": 3})
        text = await client.call_tool("drawing_digest", {})
        js = await client.call_tool("drawing_digest", {"format": "json"})
    body = text.content[0].text
    assert body.startswith("DIGEST Model: 2 entities")
    assert "WARN outside_frame 1" in body and len(body) < 500
    assert '"checks"' in js.content[0].text


# ── compact responses ───────────────────────────────────────────────────────


def test_compact_value_shrinks_entity_records_only_for_writes():
    record = {
        "handle": "2F",
        "type": "LINE",
        "layer": "0",
        "properties": {"start": [0.123456789, 0.0]},
    }
    assert is_entity_record(record)
    assert compact_value(record, write=True) == {"handle": "2F", "type": "LINE"}
    read = compact_value(record, write=False)
    assert read["properties"]["start"] == [0.1235, 0.0], "a read keeps the record, rounded"
    assert compact_value({"result": [record, record]}, write=True) == {
        "result": [{"handle": "2F", "type": "LINE"}] * 2
    }
    assert compact_value({"ok": True, "n": 1.000000001}, write=True) == {"ok": True, "n": 1.0}


@pytest_asyncio.fixture
async def compact(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_responses", "compact")
    yield
    monkeypatch.setattr(config.settings, "tool_responses", "full")
    await server._apply_tool_profile("full")


async def test_compact_mode_through_the_server(compact):
    async with Client(server.mcp) as client:
        await client.call_tool("drawing_new", {})
        made = await client.call_tool("entity_create_circle", {"cx": 1, "cy": 2, "radius": 3})
        got = await client.call_tool("entity_get", {"handle": made.structured_content["handle"]})
        about = (await client.call_tool("system_about", {})).structured_content
    assert made.structured_content == {
        "handle": made.structured_content["handle"],
        "type": "CIRCLE",
    }
    assert made.content[0].text.startswith('{"handle"')
    assert got.structured_content["properties"]["radius"] == 3.0, "entity_get keeps the record"
    assert about["tool_profile"]["responses"] == "compact"


async def test_the_spds_preset_answers_compactly(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    try:
        async with Client(server.mcp) as client:
            await client.call_tool("drawing_new", {})
            made = await client.call_tool(
                "entity_create_line", {"x1": 0, "y1": 0, "x2": 1, "y2": 1}
            )
            via_proxy = await client.call_tool(
                "call_tool",
                {
                    "name": "entity_create_ellipse",
                    "arguments": {"cx": 0, "cy": 0, "major_x": 4, "major_y": 0, "ratio": 0.5},
                },
            )
    finally:
        monkeypatch.setattr(config.settings, "tool_config", "")
        await server._apply_tool_profile("full")
    assert set(made.structured_content) == {"handle", "type"}
    assert '"properties"' not in via_proxy.content[0].text, "judged by the tool call_tool names"
