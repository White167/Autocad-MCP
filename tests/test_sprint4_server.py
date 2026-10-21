"""Regression tests for the Sprint-4 server.py fixes.

  * R20 — _registered_tool_count() uses the public FastMCP accessor
          (mcp._list_tools) and returns a real positive count, never -1/None.
  * R15 — system_about's tool_groups is derived dynamically from each tool's
          tags: it now includes the engineering/premium groups and no longer
          misfiles entity_delete_many under entity_creation.
  * N8  — drawing_plan docstring no longer claims the PlanSpec is "replayed by
          drawing_critique".

These tests drive server.py's underlying async functions directly with a
minimal fake ctx (lifespan_context + async info/warning), the same pattern the
other sprint test files use. They are ezdxf-backend-agnostic.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _Ctx:
    """Minimal async context mimicking fastmcp.Context for direct tool calls."""

    def __init__(self, backend):
        self.lifespan_context = {"backend": backend}

    async def info(self, message):
        pass

    async def warning(self, message):
        pass


# ── R20 — tool count comes from the public accessor and is a real number ─────


async def test_registered_tool_count_is_positive_never_minus_one():
    import server

    count = await server._registered_tool_count()
    assert count is not None  # never the old sentinel state
    assert isinstance(count, int)
    assert count != -1  # the old silent-failure value must never surface
    assert count > 50  # the server registers dozens of tools


async def test_registered_tools_returns_objects_with_name_and_tags():
    import server

    tools = await server._registered_tools()
    assert len(tools) > 50
    # Every entry is a real tool object usable for tag-based grouping.
    for t in tools:
        assert getattr(t, "name", None)
        # tags is a set (possibly empty) on FunctionTool
        assert hasattr(t, "tags")


# ── R15 — system_about tool_groups is dynamic + correct ──────────────────────


async def test_system_about_includes_engineering_group(backend):
    import server

    about = await server.system_about(detail=True, ctx=_Ctx(backend))
    groups = about["tool_groups"]

    # An engineering tool must appear somewhere in the breakdown (the old
    # static dict omitted all engineering tools entirely).
    all_listed = {name for names in groups.values() for name in names}
    assert "gear_draw_section_aa" in all_listed
    # The engineering group itself is present and non-empty.
    assert groups.get("engineering")
    assert "gear_draw_section_aa" in groups["engineering"]


async def test_system_about_includes_premium_group(backend):
    import server

    about = await server.system_about(detail=True, ctx=_Ctx(backend))
    groups = about["tool_groups"]
    all_listed = {name for names in groups.values() for name in names}
    # Premium meta-tools (e.g. drawing_plan) used to be missing from the dict.
    assert "drawing_plan" in all_listed
    assert groups.get("premium")


async def test_entity_delete_many_not_under_entity_creation(backend):
    import server

    about = await server.system_about(detail=True, ctx=_Ctx(backend))
    groups = about["tool_groups"]
    # The bug: entity_delete_many was hardcoded under entity_creation.
    assert "entity_delete_many" not in groups.get("entity_creation", [])
    # It is tagged {"entity", "modify"} so it belongs with modification.
    assert "entity_delete_many" in groups.get("entity_modification", [])


async def test_system_about_total_tools_matches_count(backend):
    import server

    about = await server.system_about(detail=True, ctx=_Ctx(backend))
    count = await server._registered_tool_count()
    # total_tools is reported dynamically (never hardcoded) and agrees with
    # the count helper.
    assert about.get("total_tools") == count
    assert about["total_tools"] > 50

    # The sum of all grouped tools equals the total (every tool buckets once).
    grouped = sum(len(v) for v in about["tool_groups"].values())
    assert grouped == about["total_tools"]


async def test_drawing_close_appears_in_groups(backend):
    import server

    about = await server.system_about(detail=True, ctx=_Ctx(backend))
    all_listed = {name for names in about["tool_groups"].values() for name in names}
    # drawing_close was omitted by the old static dict.
    assert "drawing_close" in all_listed


# ── R20 — system_status reports a real tool_count, never -1 ──────────────────


async def test_system_status_reports_real_tool_count(backend):
    import server

    status = await server.system_status(ctx=_Ctx(backend))
    # tool_count is present and a real positive number (omitted only if unknown).
    assert status.get("tool_count") is not None
    assert status["tool_count"] != -1
    assert status["tool_count"] > 50


# ── N8 — drawing_plan docstring corrected ────────────────────────────────────


async def test_drawing_plan_docstring_not_misleading():
    import server

    doc = server.drawing_plan.__doc__ or ""
    # The corrected docstring must not claim the plan is replayed as a critique.
    assert "replayed by `drawing_critique`" not in doc
    assert "stored on the backend" in doc
