"""What the demo СПДС sheet built through the MCP tools exposed (v1.7, AutoCAD 2024).

* ``drawing_digest`` on a sheet flagged the sheet's own viewport (DXF id 1) as
  ``outside_frame`` and ``layer_0``;
* ``cad_batch`` cut a report that names a handle (``spds_soil_hatch``'s
  ``pattern`` / ``pattern_file`` / ``letter``) down to ``{handle}``;
* ``system_about`` under the ``spds`` preset answered with about 8 000 tokens.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastmcp import Client

import config
import server

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    async with Client(server.mcp) as c:
        await c.call_tool("drawing_new", {"bootstrap": False})
        yield c


async def test_the_sheets_own_viewport_is_not_drawn_content(client):
    await client.call_tool("layout_create", {"name": "Лист9"})
    await client.call_tool("spds_sheet", {"format": "A3", "layout": "Лист9"})
    await client.call_tool(
        "viewport_create",
        {
            "layout": "Лист9",
            "center_x": 120,
            "center_y": 160,
            "width": 180,
            "height": 120,
            "view_center_x": 0,
            "view_center_y": 0,
            "scale": 0.01,
            "layer": "Defpoints",
        },
    )
    text = (
        (await client.call_tool("drawing_digest", {"space": "Лист9", "frame": [0, 0, 420, 297]}))
        .content[0]
        .text
    )
    assert "outside_frame" not in text and "layer_0" not in text
    assert "Defpoints 1: VIEWPORT 1" in text


async def test_a_batch_keeps_a_report_that_names_a_handle(client):
    out = (
        await client.call_tool(
            "cad_batch",
            {
                "steps": [
                    {
                        "tool": "spds_soil_hatch",
                        "args": {
                            "boundary": [[0, 0], [100, 0], [100, 20], [0, 20]],
                            "soil": "песок",
                            "grade": "средний",
                            "label_at": [50, 10],
                        },
                    },
                    {"tool": "entity_create_circle", "args": {"cx": 0, "cy": 0, "radius": 5}},
                ]
            },
        )
    ).structured_content
    hatch, circle = (row["result"] for row in out["results"])
    assert hatch["pattern"] == "DOTS" and hatch["letter"] == "С" and "pattern_file" in hatch
    assert set(circle) == {"handle"}, "an entity record still shrinks to its handle"


@pytest.mark.parametrize("responses", ["compact", "full"])
async def test_system_about_is_a_summary_under_compact_responses(monkeypatch, responses):
    monkeypatch.setattr(config.settings, "tool_responses", responses)
    async with Client(server.mcp) as c:
        brief = (await c.call_tool("system_about", {})).structured_content
        full = (await c.call_tool("system_about", {"detail": True})).structured_content
    assert isinstance(next(iter(full["tool_groups"].values())), list)
    if responses == "compact":
        assert isinstance(brief["tool_groups"]["spds"], int)
        assert all(
            isinstance(v, (str, type(None))) for v in brief["capabilities"]["unsupported"].values()
        )
        assert len(json.dumps(brief, ensure_ascii=False)) * 3 < len(
            json.dumps(full, ensure_ascii=False)
        )
    else:
        assert brief == full


def test_config_is_importable():  # the fixtures rely on the default settings
    assert config.settings is not None


async def test_a_stamp_graph_is_changed_in_place(client):
    await client.call_tool("layout_create", {"name": "Лист9"})
    sheet = (
        await client.call_tool(
            "spds_sheet",
            {
                "format": "A3",
                "layout": "Лист9",
                "stamp": {"code": "КП-1", "sheet": "1", "stage": "П"},
            },
        )
    ).structured_content
    out = (
        await client.call_tool(
            "spds_sheet_update", {"stamp": {"sheet": "2", "stage": "", "sheet_title": "План"}}
        )
    ).structured_content
    assert out["sheet_id"] == sheet["sheet_id"] and out["layout"] == "Лист9"
    assert out["updated"] == {"4": "План", "7": "2"} and out["cleared"] == [6]
    assert out["removed"] == 2  # graph 7 and graph 6; graph 4 had no text yet
    texts = {(row["graph"], row["text"]) for row in (await _index(client))["fields"]}
    assert ("7", "2") in {(str(g), t) for g, t in texts} and all(g != 6 for g, _t in texts)
    again = await client.call_tool(
        "spds_sheet_update", {"stamp": {"torn": "x"}}, raise_on_error=False
    )
    assert again.is_error


async def _index(client) -> dict:
    """The ACADMCP_SPDS index of the client's drawing, read through a DXF export."""
    import tempfile
    from pathlib import Path

    import ezdxf

    from engineering.spds.records import read_index

    path = Path(tempfile.mkdtemp()) / "x.dxf"
    await client.call_tool("drawing_save_as", {"path": str(path)})
    return read_index(ezdxf.readfile(path))
