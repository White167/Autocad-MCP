# tests/test_compact_listing.py
"""descriptions = "short": the listing a model reads, without changing a single call.

The short listing may drop words, never constraints: every type, enum, bound,
default and `required` entry survives, and a call is still validated by the
registered tool's full schema.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastmcp import Client

import config
import server
from discovery.compact import (
    CONVENTIONAL_PARAMS,
    PARAM_DESCRIPTION_CHARS,
    SHORT_INSTRUCTIONS,
    compact_schema,
    first_sentence,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _restore(monkeypatch):
    yield
    monkeypatch.setattr(config.settings, "tool_descriptions", "full")
    await server._apply_tool_profile("full")


def _constraints(schema):
    """Everything in a schema except the prose and the null branches."""
    if isinstance(schema, list):
        return [_constraints(s) for s in schema if s != {"type": "null"}]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in ("description", "title") or (key == "default" and value is None):
            continue
        if key == "additionalProperties" and value is False:
            continue
        if key == "anyOf":
            branches = [b for b in value if b != {"type": "null"}]
            if len(branches) == 1:
                out.update(_constraints(branches[0]))
                continue
            out[key] = _constraints(branches)
            continue
        out[key] = (
            {k: _constraints(v) for k, v in value.items()}
            if key == "properties"
            else _constraints(value)
        )
    return out


# ── the pure functions ──────────────────────────────────────────────────────


def test_first_sentence_stops_at_a_full_stop_not_inside_an_abbreviation():
    assert first_sentence("Upper deviation, e.g. 0.02. Ignored without tol_mode.") == (
        "Upper deviation, e.g. 0.02."
    )
    assert first_sentence("Modes: none, symmetric. More.") == "Modes: none, symmetric."
    long = "word " * 60
    assert len(first_sentence(long)) <= PARAM_DESCRIPTION_CHARS
    assert first_sentence(None) == ""


def test_compact_schema_collapses_null_unions_and_drops_conventional_descriptions():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "layer": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "description": "Layer name (default: current layer)",
            },
            "mode": {
                "default": "a",
                "enum": ["a", "b"],
                "type": "string",
                "description": "How it works. Long story follows here.",
            },
            "points": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}},
                "minItems": 2,
            },
        },
        "required": ["points"],
    }
    out = compact_schema(schema)
    assert out["properties"]["layer"] == {"type": "string"}
    assert out["properties"]["mode"] == {
        "default": "a",
        "enum": ["a", "b"],
        "type": "string",
        "description": "How it works.",
    }
    assert out["properties"]["points"]["minItems"] == 2
    assert out["required"] == ["points"]
    assert "additionalProperties" not in out


# ── the live surface ────────────────────────────────────────────────────────


async def _listing(mode: str):
    config.settings.tool_descriptions = mode
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        instructions = client.initialize_result.instructions
    return {t.name: t for t in tools}, instructions


async def test_short_hides_no_tool_and_keeps_every_constraint(monkeypatch):
    full, _ = await _listing("full")
    short, _ = await _listing("short")
    assert set(short) == set(full)
    for name, tool in full.items():
        assert _constraints(short[name].inputSchema) == _constraints(tool.inputSchema), name
        assert short[name].description, name


async def test_short_cuts_the_model_visible_listing_by_more_than_forty_percent():
    def visible(tools):
        return sum(
            len(json.dumps([t.name, t.description, t.inputSchema], ensure_ascii=False))
            for t in tools.values()
        )

    full, _ = await _listing("full")
    short, _ = await _listing("short")
    assert visible(short) < 0.6 * visible(full)


async def test_conventional_parameters_mean_the_convention_everywhere():
    """The short listing drops these descriptions because the instructions state
    them once. A tool whose parameter of that name meant something else would
    lose its meaning silently - so the types must agree on every tool."""
    expected = {"handle": "string", "layer": "string", "linetype": "string", "color": "integer"}
    full, _ = await _listing("full")
    for name, tool in full.items():
        props = tool.inputSchema.get("properties", {})
        for param in CONVENTIONAL_PARAMS & set(props):
            kind = compact_schema(props[param]).get("type")
            want = expected.get(param, "number")
            assert kind == want, f"{name}.{param} is {kind}, the convention is {want}"


async def test_a_short_listing_call_is_still_validated_by_the_full_schema():
    await _listing("short")
    async with Client(server.mcp) as client:
        await client.call_tool("drawing_new", {})
        bad = await client.call_tool(
            "entity_create_circle", {"cx": 0, "cy": 0, "radius": -1}, raise_on_error=False
        )
        good = await client.call_tool("entity_create_circle", {"cx": 0, "cy": 0, "radius": 5})
    assert bad.is_error, "gt=0 on radius is enforced although the listing is compact"
    assert good.structured_content["type"] == "CIRCLE"


async def test_short_swaps_the_instructions_and_full_restores_them():
    _, short = await _listing("short")
    _, full = await _listing("full")
    assert short == SHORT_INSTRUCTIONS
    assert "tool_help" in short and "gear" not in short.lower()
    assert "ENGINEERING WORKFLOW" in full


async def test_tool_help_returns_the_full_text_behind_a_short_summary():
    short, _ = await _listing("short")
    async with Client(server.mcp) as client:
        help_ = (await client.call_tool("tool_help", {"name": "drawing_diff"})).data
        missing = await client.call_tool(
            "tool_help", {"name": "dimension_diametre"}, raise_on_error=False
        )
    assert len(help_["description"]) > 5 * len(short["drawing_diff"].description)
    assert help_["parameters"]["properties"]["old_path"]["description"]
    assert help_["enabled"] is True
    assert missing.is_error and "dimension_diameter" in missing.content[0].text
