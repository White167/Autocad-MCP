"""The stage-4 acceptance run of the roadmap, on the AutoCAD of this machine.

``docs/plans/token-economy-roadmap.md``, stage 4: an A3 sheet with the
основная надпись форма 3, a 4 x 3 axis grid, three level marks and a leader
in **at most six calls**, without ``system_run_lisp``, and then
``drawing_critique(focus=None)`` with no issue. The six calls are counted:
the script fails if the drawing took more.

Drives the real server in-process (``fastmcp.Client(server.mcp)``, the stdio
path, middleware included) under the ``spds`` preset, in a **new** document it
closes unsaved; the document that was active when it started is re-activated
at the end. The operator's drawings are only listed, never modified.

After the clean critique it edits one level text by hand and checks that
``spds_elevation_format`` reports it - the focuses read the live snapshot,
not only the headless document. A picture of the sheet is written to a
temporary folder whose path is printed.

    AUTOCAD_MCP_BACKEND=com python scripts/smoke_spds_com.py
    AUTOCAD_MCP_BACKEND=com python scripts/smoke_spds_com.py --keep   # leave the document open
    python scripts/smoke_spds_com.py --headless   # the same steps on ezdxf

Exit codes: 0 every check passed, 1 something failed (the summary says what),
2 no live AutoCAD reachable.
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

#: The roadmap's budget for the sheet.
MAX_CALLS = 6
SCALE = 100
XS = [0, 6000, 12000, 18000]
YS = [0, 6000, 12000]
#: The sheet's lower-left corner in model space: at 1:100 the frame runs from
#: (-6000, -8500) to (33500, 20200) and the stamp's top edge is at y -3000,
#: clear of the grid's bottom marks (about -1800).
ORIGIN = [-8000, -9000]
STAMP = {
    "code": "СМОК-2026-АР",
    "project": "Проверка пакета СПДС",
    "building": "Жилой дом",
    "sheet_title": "План на отм. 0,000",
    "stage": "П",
    "sheet": "1",
    "sheets": "1",
    "organisation": "AutoCAD MCP Pro",
}

RESULTS: list[dict] = []


def report(step: str, ok: bool | None, detail) -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append({"step": step, "status": status, "detail": detail})
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    print(f"{status:4s}  {step}: {text[:300]}")


def data(result):
    if result.structured_content is not None:
        content = result.structured_content
        if isinstance(content, dict) and set(content) == {"result"}:
            return content["result"]
        return content
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
    folder = Path(tempfile.mkdtemp(prefix="acadmcp_spds_smoke_"))
    async with Client(server.mcp) as c:
        status = data(await call(c, "system_status"))
        live = isinstance(status, dict) and status.get("backend") == "com"
        if not live and not headless:
            print(f"no live AutoCAD reachable (backend: {(status or {}).get('backend')})")
            return 2
        print(f"pictures: {folder}")
        report("connect", None, {k: status.get(k) for k in ("autocad_version", "active_document")})
        docs = data(await call(c, "document_list"))
        start = next(
            (d["name"] for d in (docs or {}).get("documents", []) if d.get("active")), None
        )

        await call(c, "drawing_new", {"bootstrap": False})
        try:
            calls = 0

            async def draw(tool, args):
                nonlocal calls
                calls += 1
                return data(await call(c, tool, args))

            sheet = await draw(
                "spds_sheet",
                {"format": "A3", "stamp": STAMP, "origin": ORIGIN, "scale": SCALE},
            )
            report("1 spds_sheet A3 form 3", sheet.get("ok") is True, sheet.get("frame"))
            grid = await draw("spds_axis_grid", {"xs": XS, "ys": YS, "scale": SCALE})
            report(
                "2 spds_axis_grid 4 x 3",
                list(grid.get("vertical", {})) == ["1", "2", "3", "4"]
                and list(grid.get("horizontal", {})) == ["А", "Б", "В"],
                {k: grid.get(k) for k in ("vertical", "horizontal")},
            )
            levels = []
            for x, y, value in ((3000, 3000, 0.0), (9000, 3000, -0.15), (15000, 9000, 3.3)):
                level = await draw(
                    "spds_elevation",
                    {"x": x, "y": y, "value": value, "kind": "plan", "scale": SCALE},
                )
                levels.append(level)
            report(
                "3-5 spds_elevation x3",
                [lv.get("text") for lv in levels] == ["0,000", "-0,150", "+3,300"],
                [lv.get("text") for lv in levels],
            )
            leader = await draw(
                "spds_leader",
                {
                    "points": [[9000, 9000], [10500, 10500]],
                    "text": "Кирпич М150",
                    "text_below": "на р-ре М75",
                    "scale": SCALE,
                },
            )
            report("6 spds_leader", leader.get("ok") is True, leader.get("entities"))
            report(f"at most {MAX_CALLS} calls, no LISP", calls <= MAX_CALLS, {"calls": calls})

            issues = data(await call(c, "drawing_critique", {"focus": None})) or []
            report(
                "drawing_critique(focus=None) clean",
                issues == [],
                [{k: i.get(k) for k in ("focus", "message")} for i in issues][:6],
            )

            # the focuses read the live drawing: a hand-edited level is caught
            text_handle = (levels[2].get("handles") or [None])[-1]
            await call(c, "entity_edit_text", {"handle": text_handle, "text": "3.30"})
            edited = (
                data(await call(c, "drawing_critique", {"focus": ["spds_elevation_format"]})) or []
            )
            report(
                "spds_elevation_format catches a hand edit",
                len(edited) == 1 and edited[0].get("detail", {}).get("expected") == "+3,300",
                [i.get("message") for i in edited],
            )

            shot = await call(
                c,
                "view_screenshot",
                {"window": [-8000, -9000, 34000, 20700], "mode": "mono", "width": 1200},
                expect_error=True,
            )
            if shot.is_error:  # the picture is for the operator, not a check
                report("picture", None, shot.content[0].text[:200])
            else:
                (folder / "sheet.png").write_bytes(base64.b64decode(shot.content[0].data))
                report("picture", None, str(folder / "sheet.png"))
        finally:
            if not keep:
                closed = await call(c, "drawing_close", {"save": False}, expect_error=True)
                report("close the smoke document", not closed.is_error, data(closed))
                if start:
                    await call(c, "document_activate", {"name_or_path": start}, expect_error=True)

    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    print(json.dumps({"failed": failed, "pictures": str(folder)}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    try:
        args = sys.argv[1:]
        return asyncio.run(run("--keep" in args, headless="--headless" in args))
    except ImportError as exc:
        print(f"pywin32 / fastmcp not available: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
