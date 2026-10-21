# tests/test_tool_config.py
"""TOOL_CONFIG: one file (or a bundled preset) that says which tools a client sees.

The keys a file sets win over TOOL_PROFILE / TOOL_PACKS / TOOL_DESCRIPTIONS;
the ones it leaves out fall back to them. A config that cannot be read never
blocks startup - it is reported in system_about.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client

import config
import server
from discovery.profiles import (
    PRESETS_DIR,
    ToolConfigError,
    load_tool_config,
    preset_names,
)

pytestmark = pytest.mark.asyncio

GEAR_AND_GDT = set(server.PACK_TOOL_NAMES["gear"] | server.PACK_TOOL_NAMES["gdt"])
ISO_SHEET = {"sheet_frame", "titleblock_apply", "titleblock_apply_iso_a3", "bom_table"}


@pytest_asyncio.fixture(autouse=True)
async def _restore(monkeypatch):
    yield
    monkeypatch.setattr(config.settings, "tool_config", "")
    monkeypatch.setattr(config.settings, "tool_packs", "all")
    monkeypatch.setattr(config.settings, "tool_descriptions", "full")
    await server._apply_tool_profile("full")


# ── loading ─────────────────────────────────────────────────────────────────


def test_the_three_presets_ship_inside_the_package():
    assert preset_names() == ["drafting", "review", "spds"]
    assert PRESETS_DIR.parent.name == "discovery", "discovery/ is in the wheel's only-include"


def test_an_empty_value_is_no_config():
    assert load_tool_config("").is_empty
    assert load_tool_config(None).is_empty


def test_an_unknown_name_is_refused_with_the_presets():
    with pytest.raises(ToolConfigError, match="drafting, review, spds"):
        load_tool_config("gost3000")


def test_a_missing_toml_path_is_refused_by_path(tmp_path):
    with pytest.raises(ToolConfigError, match="not found"):
        load_tool_config(str(tmp_path / "nope.toml"))


def test_a_file_is_parsed_and_unknown_keys_are_reported(tmp_path):
    path = tmp_path / "mine.toml"
    path.write_text(
        'packs = "core,pid"\nexclude = ["entity_create_line"]\n'
        'exclude_groups = ["GEAR"]\ndescriptions = "SHORT"\nfavourite = 3\n',
        encoding="utf-8",
    )
    cfg = load_tool_config(str(path))
    assert cfg.source == "file"
    assert cfg.packs == ("core", "pid")
    assert cfg.exclude == ("entity_create_line",)
    assert cfg.exclude_groups == ("gear",), "tags are folded, tool names are not"
    assert cfg.descriptions == "short"
    assert cfg.ignored_keys == ("favourite",)


def test_a_bad_descriptions_value_is_refused(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('descriptions = "tiny"\n', encoding="utf-8")
    with pytest.raises(ToolConfigError, match="full, short"):
        load_tool_config(str(path))


def test_a_file_that_does_not_parse_is_refused(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("packs = [\n", encoding="utf-8")
    with pytest.raises(ToolConfigError):
        load_tool_config(str(path))


# ── applying ────────────────────────────────────────────────────────────────


async def test_the_spds_preset_drops_verticals_gears_and_the_iso_sheet(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    info = await server._apply_tool_profile()
    disabled = set(info["disabled_tools"])
    assert GEAR_AND_GDT <= disabled
    assert ISO_SHEET <= disabled
    assert server.PACK_TOOL_NAMES["pid"] <= disabled
    assert server.PACK_TOOL_NAMES["arch"] <= disabled
    # include lifts a settings-pack tool back over the packs = ["core"] line
    assert "document_list" not in disabled and "document_activate" not in disabled
    assert "document_close" in disabled
    for essential in ("entity_create_line", "dimension_linear", "layout_create", "tool_help"):
        assert essential not in disabled, essential
    assert info["descriptions"] == "short"
    assert info["tool_config"]["source"] == "preset:spds"


async def test_the_review_preset_keeps_only_reads_plus_diff(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "review")
    info = await server._apply_tool_profile()
    tools = {t.name: t for t in await server._registered_tools()}
    enabled = set(tools) - set(info["disabled_tools"])
    assert "drawing_diff" in enabled
    for name in enabled - {"drawing_diff"}:
        assert tools[name].meta["cad"]["cost"] == "read", name
    assert "entity_create_line" not in enabled


async def test_file_keys_win_over_the_environment(monkeypatch, tmp_path):
    path = tmp_path / "lean.toml"
    path.write_text('base = "lean"\npacks = ["core"]\n', encoding="utf-8")
    monkeypatch.setattr(config.settings, "tool_profile", "full")
    monkeypatch.setattr(config.settings, "tool_packs", "all")
    monkeypatch.setattr(config.settings, "tool_config", str(path))
    info = await server._apply_tool_profile()
    assert info["profile"] == "lean"
    assert info["tool_packs"]["enabled"] == ["core"]
    assert info["descriptions"] == "full", "no descriptions key: TOOL_DESCRIPTIONS applies"


async def test_include_wins_over_exclude_but_not_over_the_3d_gate(monkeypatch, tmp_path):
    path = tmp_path / "inc.toml"
    path.write_text(
        'exclude_groups = ["create"]\ninclude = ["entity_create_line", "solid_box", "no_such"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.settings, "tool_config", str(path))
    monkeypatch.setattr(config.settings, "enable_3d", False)
    info = await server._apply_tool_profile()
    disabled = set(info["disabled_tools"])
    assert "entity_create_line" not in disabled
    assert "entity_create_circle" in disabled
    assert "solid_box" in disabled, "a disabled solid would only refuse the call"
    assert info["tool_config"]["unmatched"] == {"include": ["no_such"]}


async def test_an_unreadable_config_falls_back_and_says_why(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "gost3000")
    info = await server._apply_tool_profile()
    assert info["profile"] == "full"
    assert "gost3000" in info["tool_config"]["error"]
    assert set(info["disabled_tools"]) <= server.SOLID_TOOL_NAMES


async def test_system_about_reports_the_config(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "drafting")
    async with Client(server.mcp) as client:
        about = (await client.call_tool("system_about", {})).structured_content
        names = {t.name for t in await client.list_tools()}
    assert about["tool_profile"]["tool_config"]["source"] == "preset:drafting"
    assert not (GEAR_AND_GDT & names)
    assert "drawing_finalize" in names


# ── discovery = "hybrid" ────────────────────────────────────────────────────


async def test_every_spds_pin_names_a_tool_the_preset_enables(monkeypatch):
    """A pin the filters hide would be dropped without a word; refuse that here."""
    cfg = load_tool_config("spds")
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    info = await server._apply_tool_profile()
    disabled = set(info["disabled_tools"])
    assert cfg.pinned and not (set(cfg.pinned) & disabled)
    assert info["discovery"]["pinned_count"] == len(set(cfg.pinned))


async def test_hybrid_lists_the_pins_and_reaches_the_rest_through_search(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    async with Client(server.mcp) as client:
        listed = {t.name for t in await client.list_tools()}
        found = (await client.call_tool("search_tools", {"query": "круговой массив"})).content
        gear = (await client.call_tool("search_tools", {"query": "spur gear front view"})).content
        await client.call_tool("drawing_new", {})
        circle = await client.call_tool(
            "call_tool",
            {"name": "entity_create_circle", "arguments": {"cx": 0, "cy": 0, "radius": 4}},
        )
        hidden = await client.call_tool(
            "call_tool",
            {"name": "gear_draw_spur_front_view", "arguments": {}},
            raise_on_error=False,
        )
    pinned = set(load_tool_config("spds").pinned)
    assert listed == pinned | {"search_tools", "call_tool"}
    assert "entity_array_polar" in found[0].text, "a non-pinned enabled tool is searchable"
    assert "gear_draw" not in gear[0].text, "a tool the preset disables is not"
    assert not circle.is_error
    assert hidden.is_error


async def test_a_profile_without_discovery_restores_the_environment_mode(monkeypatch):
    monkeypatch.setattr(config.settings, "tool_config", "spds")
    await server._apply_tool_profile()
    monkeypatch.setattr(config.settings, "tool_config", "")
    info = await server._apply_tool_profile()
    assert "discovery" not in info
    async with Client(server.mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "search_tools" not in names and len(names) > 200


async def test_discovery_mode_hybrid_pins_the_lean_set(monkeypatch):
    monkeypatch.setattr(config.settings, "discovery_mode", "hybrid")
    try:
        info = await server._apply_tool_profile("full")
        async with Client(server.mcp) as client:
            names = {t.name for t in await client.list_tools()}
    finally:
        monkeypatch.setattr(config.settings, "discovery_mode", "off")
        await server._apply_tool_profile("full")
    assert info["discovery"]["mode"] == "hybrid"
    assert names == set(server.LEAN_TOOL_NAMES) | {"search_tools", "call_tool"}
