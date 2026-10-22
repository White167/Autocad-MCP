# tests/test_spds_critique.py
"""The six `spds_*` critique focuses: silent without ACADMCP_SPDS records,
silent on a sheet the pack drew itself, and each one firing on the defect it
names - end to end on the headless engine, plus the pure rules."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client

import server
from engineering.plan_spec import ALL_CRITIQUE_FOCUSES
from engineering.spds.critique import (
    SPDS_FOCUSES,
    axis_rank,
    is_series_height,
    issues_for,
)

FULL_STAMP = {
    "code": "КП-2026-КЖ",
    "project": "Жилой дом",
    "sheet_title": "План на отм. 0,000",
    "stage": "П",
    "sheet": "1",
}


@pytest_asyncio.fixture
async def client():
    async with Client(server.mcp) as c:
        await c.call_tool("drawing_new", {"bootstrap": False})
        yield c


async def _spds_issues(client, focus=None) -> list[dict]:
    out = await client.call_tool("drawing_critique", {"focus": focus})
    rows = out.structured_content
    rows = rows.get("result", rows) if isinstance(rows, dict) else rows
    return [row for row in rows if str(row["focus"]).startswith("spds_")]


async def _sheet_and_plan(client) -> dict:
    await client.call_tool("spds_setup", {"scales": [100]})
    await client.call_tool("layout_create", {"name": "Лист1"})
    sheet = (
        await client.call_tool(
            "spds_sheet", {"format": "A3", "layout": "Лист1", "stamp": FULL_STAMP}
        )
    ).structured_content
    await client.call_tool(
        "spds_axis_grid", {"xs": [0, 6000, 12000, 18000], "ys": [0, 6000, 12000], "scale": 100}
    )
    level = (
        await client.call_tool("spds_elevation", {"x": 0, "y": 0, "value": 3.3, "scale": 100})
    ).structured_content
    return {"sheet": sheet, "level": level}


def test_the_six_focuses_are_in_the_closed_enum():
    assert len(SPDS_FOCUSES) == 6
    for focus in SPDS_FOCUSES:
        assert focus in ALL_CRITIQUE_FOCUSES


def test_series_heights_and_axis_ranks():
    assert is_series_height(3.5) and is_series_height(2.5001)
    assert not is_series_height(3.0)
    assert axis_rank("12") == ("digit", 12)
    assert axis_rank("А") < axis_rank("Б") < axis_rank("Я") < axis_rank("АА")
    assert axis_rank("1/1") is None and axis_rank("З") is None


def test_every_focus_is_silent_without_a_record():
    index = {
        "sheets": [],
        "fields": [],
        "marks": [],
        "texts": [
            {"handle": "1", "layout": "Model", "layer": "Текст", "height": 3.0, "tagged": False}
        ],
        "layer_weights": {"Контур": 0.3},
        "weights": [],
        "outside": [],
    }
    for focus in SPDS_FOCUSES:
        assert issues_for(focus, index) == []


@pytest.mark.asyncio
async def test_a_plain_drawing_gets_no_spds_issue(client):
    await client.call_tool("spds_setup", {"scales": [100]})
    await client.call_tool(
        "entity_create_text", {"text": "x", "x": 0, "y": 0, "height": 3.0, "layer": "Текст"}
    )
    assert await _spds_issues(client) == []


@pytest.mark.asyncio
async def test_a_sheet_drawn_by_the_pack_is_clean(client):
    await _sheet_and_plan(client)
    assert await _spds_issues(client) == []


@pytest.mark.asyncio
async def test_text_height_off_the_series(client):
    await _sheet_and_plan(client)
    await client.call_tool(
        "spds_leader", {"points": [[0, 0], [500, 500]], "text": "Ф", "height": 3.0, "scale": 100}
    )
    (issue,) = await _spds_issues(client, ["spds_text_height"])
    assert issue["detail"]["paper_height"] == 3.0


@pytest.mark.asyncio
async def test_untagged_text_is_judged_at_the_recorded_scales(client):
    await _sheet_and_plan(client)
    await client.call_tool(
        "entity_create_text", {"text": "ok", "x": 0, "y": 0, "height": 350, "layer": "Текст"}
    )
    assert await _spds_issues(client, ["spds_text_height"]) == []
    await client.call_tool(
        "entity_create_text", {"text": "no", "x": 0, "y": 0, "height": 300, "layer": "Текст"}
    )
    (issue,) = await _spds_issues(client, ["spds_text_height"])
    assert issue["detail"]["scales"] == [100.0]


@pytest.mark.asyncio
async def test_lineweight_of_a_layer_and_of_an_entity(client):
    await _sheet_and_plan(client)
    await client.call_tool("layer_modify", {"name": "Контур", "lineweight": 30})
    line = (
        await client.call_tool(
            "entity_create_line", {"x1": 0, "y1": 0, "x2": 1, "y2": 0, "layer": "Тонкие"}
        )
    ).structured_content
    await client.call_tool("entity_set_properties", {"handle": line["handle"], "lineweight": 13})
    issues = await _spds_issues(client, ["spds_lineweight"])
    assert {i["detail"].get("layer") for i in issues} == {"Контур", "Тонкие"}
    assert [i["handles"] for i in issues if i["handles"]] == [[line["handle"]]]


@pytest.mark.asyncio
async def test_axis_letters_forbidden_and_out_of_order(client):
    await _sheet_and_plan(client)
    await client.call_tool(
        "spds_axis", {"x1": -6000, "y1": 0, "x2": -6000, "y2": 1000, "mark": "5"}
    )
    issues = await _spds_issues(client, ["spds_axis_letter"])
    assert issues and all(i["severity"] == "error" for i in issues)
    assert any("5" in i["detail"]["labels"] for i in issues)


def test_a_forbidden_letter_is_named():
    index = {
        "sheets": [],
        "fields": [],
        "marks": [
            {
                "kind": "mark",
                "mark": "axis",
                "label": "З",
                "dir": "y",
                "at": 0.0,
                "handle": "A",
                "layout": "Model",
            },
        ],
        "texts": [],
        "layer_weights": {},
        "weights": [],
        "outside": [],
    }
    (issue,) = issues_for("spds_axis_letter", index)
    assert issue.detail["forbidden"] == ["З"]


@pytest.mark.asyncio
async def test_a_hand_edited_level_is_reported(client):
    drawn = await _sheet_and_plan(client)
    assert drawn["level"]["text"] == "+3,300"
    await client.call_tool(
        "entity_edit_text", {"handle": drawn["level"]["handles"][-1], "text": "3.30"}
    )
    (issue,) = await _spds_issues(client, ["spds_elevation_format"])
    assert issue["detail"]["expected"] == "+3,300" and issue["detail"]["text"] == "3.30"


@pytest.mark.asyncio
async def test_an_incomplete_stamp(client):
    await client.call_tool("layout_create", {"name": "Лист2"})
    await client.call_tool(
        "spds_sheet", {"format": "A3", "layout": "Лист2", "stamp": {"code": "X"}}
    )
    (issue,) = await _spds_issues(client, ["spds_stamp_incomplete"])
    assert issue["detail"]["missing"] == [2, 4, 6, 7]
    await client.call_tool(
        "spds_sheet_update",
        {"stamp": {"project": "a", "sheet_title": "b", "stage": "П", "sheet": "2"}},
    )
    assert await _spds_issues(client, ["spds_stamp_incomplete"]) == []


@pytest.mark.asyncio
async def test_an_entity_past_the_frame_of_a_layout_sheet(client):
    await _sheet_and_plan(client)
    await client.call_tool("layout_set_current", {"name": "Лист1"})
    inside = (
        await client.call_tool("entity_create_line", {"x1": 30, "y1": 100, "x2": 300, "y2": 100})
    ).structured_content
    past = (
        await client.call_tool("entity_create_line", {"x1": 30, "y1": 200, "x2": 418, "y2": 200})
    ).structured_content
    (issue,) = await _spds_issues(client, ["spds_outside_frame"])
    assert issue["handles"] == [past["handle"]] and inside["handle"] not in issue["handles"]
