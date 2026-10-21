"""One live run of everything v1.7 added, on the AutoCAD of this machine.

Drives the real server in-process (``fastmcp.Client(server.mcp)``, the same
path a stdio client takes, middleware included) with the live engine, in a
**new** document it creates and closes unsaved; the document that was active
when it started is re-activated at the end. The operator's drawings are only
listed, never modified.

Each step prints one line - ``PASS`` / ``FAIL`` / ``INFO`` and what came back -
and the JSON summary at the end lists every failure. The rendered pictures
are written to a temporary folder whose path is printed.

    AUTOCAD_MCP_BACKEND=com python scripts/smoke_v17_com.py
    AUTOCAD_MCP_BACKEND=com python scripts/smoke_v17_com.py --keep   # leave the document open
    python scripts/smoke_v17_com.py --headless   # the same steps on ezdxf, live-only ones skipped

Exit codes: 0 every check passed, 1 something failed (the summary says what),
2 no live AutoCAD reachable.

What it checks, by roadmap stage:

* 1  the spds preset lists the СПДС pack; tool_help answers
* 2  drawing_checkpoint / drawing_diff_since see exactly the edits made;
     drawing_digest reports the planted outside-of-frame circle
* 3  view_screenshot(window=...) renders from a snapshot and the AutoCAD view
     (VIEWCTR / VIEWSIZE) does not move
* 5  system_run_lisp returns the value of (+ 40 2) and an AutoLISP error as an
     error; entity_create_text style / align; dimension_* dimstyle (and the
     current style restored); a fresh layout gets no viewport of AutoCAD's own;
     entity_edit_geometry moves a polyline vertex
* 4  spds_setup, spds_sheet on a layout, spds_axis_grid, spds_elevation,
     spds_leader
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "--headless" in sys.argv[1:]:
    os.environ["AUTOCAD_MCP_BACKEND"] = "ezdxf"
os.environ.setdefault("AUTOCAD_MCP_BACKEND", "com")

RESULTS: list[dict] = []


def report(step: str, ok: bool | None, detail) -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append({"step": step, "status": status, "detail": detail})
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    print(f"{status:4s}  {step}: {text[:300]}")


def data(result):
    if result.structured_content is not None:
        return result.structured_content
    return result.content[0].text if result.content else None


async def call(client, tool, args=None, *, expect_error=False):
    out = await client.call_tool(tool, args or {}, raise_on_error=False)
    if out.is_error and not expect_error:
        raise RuntimeError(f"{tool}: {out.content[0].text if out.content else out}")
    return out


async def run(keep: bool, headless: bool = False) -> int:
    from fastmcp import Client

    import config
    import server

    config.settings.tool_config = "spds"
    async with Client(server.mcp) as c:
        status = data(await call(c, "system_status"))
        live = isinstance(status, dict) and status.get("backend") == "com"
        if not live and not headless:
            print(f"no live AutoCAD reachable (backend: {(status or {}).get('backend')})")
            return 2
        folder = Path(tempfile.mkdtemp(prefix="acadmcp_v17_smoke_"))
        print(f"pictures: {folder}")
        report("connect", None, {k: status.get(k) for k in ("autocad_version", "active_document")})
        docs = data(await call(c, "call_tool", {"name": "document_list", "arguments": {}}))
        start = next(
            (d["name"] for d in (docs or {}).get("documents", []) if d.get("active")), None
        )

        # stage 1
        names = {t.name for t in await c.list_tools()}
        report(
            "preset lists the spds pack", {"spds_sheet", "spds_setup"} <= names, sorted(names)[:8]
        )
        helped = data(await call(c, "tool_help", {"name": "spds_sheet"}))
        report("tool_help", bool(helped.get("description")), helped.get("summary"))

        await call(c, "drawing_new", {"bootstrap": False})
        try:
            # stage 5: LISP (live only)
            if live:
                lisp = data(await call(c, "system_run_lisp", {"expression": "(+ 40 2)"}))
                report("run_lisp value", lisp.get("result") == "42", lisp)
                err = data(await call(c, "system_run_lisp", {"expression": "(+ nil 1)"}))
                report("run_lisp error", err.get("ok") is False and bool(err.get("error")), err)

            # stage 4: setup
            setup = data(await call(c, "spds_setup", {"scales": [100], "current_scale": 100}))
            report("spds_setup", setup.get("ok") is True, setup.get("dimstyles"))
            again = data(await call(c, "spds_setup", {"scales": [100]}))
            report(
                "spds_setup idempotent",
                again.get("layers_created") == [],
                again.get("layers_created"),
            )

            # stage 2: checkpoint before the model edits
            await call(c, "drawing_checkpoint", {"name": "smoke"})

            # stage 5: text and dimension style
            text = data(
                await call(
                    c,
                    "entity_create_text",
                    {"text": "А-А", "x": 0, "y": 0, "height": 500, "style": "GOST", "align": "MC"},
                )
            )
            dump = data(
                await call(
                    c,
                    "call_tool",
                    {"name": "analysis_list_properties", "arguments": {"handle": text["handle"]}},
                )
            )
            attrs = (dump or {}).get("dxf_attributes", {})
            # headless: DXF names (style, halign); live: ActiveX members (styleName,
            # no alignment member in the dump - the centred bbox stands in for it)
            style = str(attrs.get("style") or attrs.get("styleName") or "")
            box = ((dump or {}).get("properties") or {}).get("bounding_box") or {}
            centred = abs(sum(box.get("min", [0, 0])[:1] + box.get("max", [0, 0])[:1])) < 50
            report(
                "text style + align",
                style.upper() == "GOST" and attrs.get("halign", 1) == 1 and centred,
                {"style": style, "halign": attrs.get("halign"), "bbox": box},
            )
            dim = data(
                await call(
                    c,
                    "dimension_linear",
                    {
                        "x1": 0,
                        "y1": -3000,
                        "x2": 6000,
                        "y2": -3000,
                        "dim_x": 3000,
                        "dim_y": -4500,
                        "layer": "Размеры",
                        "dimstyle": "Standard",
                    },
                )
            )
            styles = data(await call(c, "call_tool", {"name": "dimstyle_list", "arguments": {}}))
            report(
                "dimstyle per call, current restored",
                styles.get("current") == "СПДС_1-100",
                {"dimension": dim, "current": styles.get("current")},
            )

            # stage 4: marks
            grid = data(
                await call(
                    c, "spds_axis_grid", {"xs": [0, 6000, 12000], "ys": [0, 7200], "scale": 100}
                )
            )
            report("spds_axis_grid", grid.get("entities") == 5 * 3, grid)
            level = data(
                await call(c, "spds_elevation", {"x": 14000, "y": 0, "value": -1.65, "scale": 100})
            )
            report("spds_elevation", level.get("text") == "-1,650", level)
            leader = data(
                await call(
                    c,
                    "spds_leader",
                    {"points": [[3000, 3000], [5000, 5000]], "text": "Бетон В25", "scale": 100},
                )
            )
            report("spds_leader", leader.get("entities") == 4, leader)

            section = data(
                await call(
                    c,
                    "spds_section_mark",
                    {
                        "x1": -1500,
                        "y1": 5600,
                        "x2": 13500,
                        "y2": 5600,
                        "label": "1",
                        "side": -1,
                        "scale": 100,
                    },
                )
            )
            report("spds_section_mark", section.get("title") == "1-1", section)
            slope = data(
                await call(c, "spds_slope", {"x": 3000, "y": 200, "value": 0.005, "scale": 100})
            )
            report("spds_slope", slope.get("text") == "0,005", slope)
            soil = data(
                await call(
                    c,
                    "spds_soil_hatch",
                    {
                        "boundary": [[0, -3000], [12000, -3000], [12000, -1500], [0, -1500]],
                        "soil": "суглинок",
                        "scale": 100,
                    },
                )
            )
            report("spds_soil_hatch", soil.get("pattern") == "ANSI31", soil)

            # stage 5: polyline vertex
            poly = data(
                await call(
                    c, "entity_create_polyline", {"points": [[0, 0], [0, 1000]], "layer": "Контур"}
                )
            )
            await call(
                c,
                "entity_edit_geometry",
                {"handle": poly["handle"], "vertex_index": -1, "vy": -2000},
            )
            got = data(await call(c, "entity_get", {"handle": poly["handle"]}))
            report("polyline vertex edit", "-2000" in json.dumps(got), got.get("properties"))

            # stage 2: the diff since the checkpoint, and the digest
            diff = (await call(c, "drawing_diff_since", {"name": "smoke"})).content[0].text
            report(
                "diff_since lists the edits",
                diff.startswith('SINCE "smoke"') and "+ " in diff,
                diff.splitlines()[0],
            )
            await call(
                c, "entity_create_circle", {"cx": 900000, "cy": 0, "radius": 10, "layer": "Контур"}
            )
            digest = (
                (await call(c, "drawing_digest", {"frame": [-5000, -8000, 30000, 12000]}))
                .content[0]
                .text
            )
            report("digest flags outside_frame", "outside_frame" in digest, digest.splitlines()[0])

            # stage 3: framed screenshot does not move the view (the view is live only)
            window = {"window": [-2000, -5000, 15000, 9000], "mode": "mono", "width": 600}
            if live:
                before = data(await call(c, "system_get_variable", {"name": "VIEWCTR"}))
                size_before = data(await call(c, "system_get_variable", {"name": "VIEWSIZE"}))
            shot = await call(c, "view_screenshot", window)
            (folder / "framed.png").write_bytes(base64.b64decode(shot.content[0].data))
            if live:
                after = data(await call(c, "system_get_variable", {"name": "VIEWCTR"}))
                size_after = data(await call(c, "system_get_variable", {"name": "VIEWSIZE"}))
                report(
                    "framed screenshot keeps the view",
                    (before, size_before) == (after, size_after),
                    {"before": [before, size_before], "after": [after, size_after]},
                )
            else:
                report("framed screenshot", bool(shot.content), "rendered headlessly")

            # stage 5 + 4: a fresh layout, the sheet, a viewport with no AutoCAD viewport on top
            if live:
                lcv_before = data(
                    await call(c, "system_get_variable", {"name": "LAYOUTCREATEVIEWPORT"})
                )
            await call(c, "layout_create", {"name": "СмокЛист"})
            sheet = data(
                await call(
                    c,
                    "spds_sheet",
                    {
                        "format": "A3",
                        "layout": "СмокЛист",
                        "stamp": {
                            "code": "SMOKE-КЖ",
                            "project": "Проверка 1.7",
                            "stage": "У",
                            "sheet": "1",
                            "sheets": "1",
                        },
                        "signatures": [["Разраб.", "Smoke", ""]],
                    },
                )
            )
            report(
                "spds_sheet on a layout",
                sheet.get("entities", 0) > 40,
                {k: sheet.get(k) for k in ("frame", "fitted")},
            )
            vp = data(
                await call(
                    c,
                    "viewport_create",
                    {
                        "layout": "СмокЛист",
                        "center_x": 120,
                        "center_y": 160,
                        "width": 180,
                        "height": 120,
                        "view_center_x": 6000,
                        "view_center_y": 3000,
                        "scale": 0.01,
                    },
                )
            )
            listed = data(await call(c, "viewport_list", {"layout": "СмокЛист"}))
            # Besides ours only the sheet's own paper-space viewport may be
            # there. ezdxf flags it is_main; ActiveX cannot (is_main is null),
            # so live one unflagged extra is the sheet itself - AutoCAD's own
            # viewport would make it two (seen on AutoCAD 2024 before the
            # LAYOUTCREATEVIEWPORT write went over as a short).
            extra = [
                v["handle"]
                for v in (listed or {}).get("viewports", [])
                if v.get("handle") != vp.get("handle") and v.get("is_main") is not True
            ]
            report(
                "no AutoCAD viewport on the new layout",
                len(extra) <= (1 if live else 0),
                {"created": vp.get("handle"), "others": extra},
            )
            if live:
                lcv_after = data(
                    await call(c, "system_get_variable", {"name": "LAYOUTCREATEVIEWPORT"})
                )
                report(
                    "operator's LAYOUTCREATEVIEWPORT restored",
                    lcv_before == lcv_after,
                    {"before": lcv_before, "after": lcv_after},
                )
            sheet_shot = await call(
                c, "view_screenshot", {"space": "СмокЛист", "mode": "mono", "width": 1000}
            )
            (folder / "sheet.png").write_bytes(base64.b64decode(sheet_shot.content[0].data))
        finally:
            if not keep:
                closed = await call(c, "drawing_close", {"save": False}, expect_error=True)
                report("close the smoke document", not closed.is_error, data(closed))
                if start:
                    await call(
                        c,
                        "call_tool",
                        {"name": "document_activate", "arguments": {"name_or_path": start}},
                        expect_error=True,
                    )

    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    print(json.dumps({"failed": failed, "pictures": str(folder)}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    try:
        args = sys.argv[1:]
        if "--headless" in args:
            os.environ["AUTOCAD_MCP_BACKEND"] = "ezdxf"
        return asyncio.run(run("--keep" in args, headless="--headless" in args))
    except ImportError as exc:
        print(f"pywin32 / fastmcp not available: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
