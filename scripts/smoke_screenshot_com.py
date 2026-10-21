"""Exercise the v1.7 screenshot paths once against the live AutoCAD on this machine.

Writes a small DXF into a temporary folder, opens it in the running AutoCAD
and, with that document active:

    VIEWCTR / VIEWSIZE                         -> the operator-visible view, before
    view_render(window, overlay_handles)       -> an offscreen render of a
                                                  Document.Export snapshot
    VIEWCTR / VIEWSIZE                         -> must be unchanged: the render
                                                  never zooms or regenerates
    view_screenshot()  + postprocess(mono)     -> the window capture, 800 px,
                                                  inverted to ink on white

and prints what came back as JSON (sizes, labels, the view before and after,
the image token estimates). The PNGs are written next to the DXF in the
temporary folder, whose path is printed, so they can be looked at. Exit codes:

    0  the render has the window's aspect and the requested width, labels
       the one entity inside the window, and the view did not move; the
       capture came back and post-processed
    1  it ran, but something disagreed (the JSON says what)
    2  no live CAD application reachable (pywin32 missing, or no running
       instance to attach to)

The operator's drawings are never touched: this reuses the guards of
``scripts/smoke_understand_com.py`` - attach only to a running instance,
work only in a document it opened from its own folder, check that document is
active inside every current-document call, close it unsaved on the way out
and re-activate the document that was active at the start.

    AUTOCAD_MCP_BACKEND=com uv run --frozen python scripts/smoke_screenshot_com.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "smoke_understand_com", ROOT / "scripts" / "smoke_understand_com.py"
)
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)


def _write_scene(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    for i in range(6):
        msp.add_circle((40 + 60 * i, 150), 20)
    msp.add_text("SMOKE", dxfattribs={"height": 7}).set_placement((10, 200))
    doc.saveas(path)


def _view(backend):
    def _sync():
        from backends import com_backend as com

        doc = com._acad_doc()
        center = doc.GetVariable("VIEWCTR")
        return {
            "center": [round(float(center[0]), 6), round(float(center[1]), 6)],
            "size": round(float(doc.GetVariable("VIEWSIZE")), 6),
        }

    return backend._run(_sync)


async def _smoke(backend, folder: Path) -> dict:
    from PIL import Image

    from backends.render import image_tokens, postprocess

    path = folder / "smoke_screenshot.dxf"
    _write_scene(path)
    key = await guards._open(backend, path)
    async with guards._Current(key):
        before = await _view(backend)
        render = await backend.view_render(
            window=(10, 120, 130, 180), width=600, mode="mono", overlay_handles=True
        )
        after = await _view(backend)
        capture = await backend.view_screenshot()
    (folder / "render.png").write_bytes(render["png"])
    report: dict = {
        "folder": str(folder),
        "view_before": before,
        "view_after": after,
        "render": {
            "size": [render["width"], render["height"]],
            "labelled": render["labelled"],
            "image_tokens": image_tokens(render["width"], render["height"]),
        },
    }
    if capture is not None:
        raw = Image.open(io.BytesIO(capture))
        data, info = postprocess(capture, width=800, mode="mono")
        (folder / "capture_mono.png").write_bytes(data)
        report["capture"] = {
            "raw_size": list(raw.size),
            "raw_image_tokens": image_tokens(*raw.size),
            **info,
        }
    else:
        report["capture"] = None
    return report


async def main() -> int:
    try:
        from backends.com_backend import ComBackend
    except ImportError as exc:
        print(f"pywin32 not available: {exc}")
        return 2
    backend = ComBackend()
    try:
        await backend.connect()
    except Exception as exc:
        print(f"no live AutoCAD reachable: {exc}")
        return 2
    guards._patient(backend)
    folder = Path(tempfile.mkdtemp(prefix="acadmcp_screenshot_smoke_"))
    guards._STATE["folder"] = str(folder)
    try:
        version = await guards._attach(backend)
        if version is None:
            print("no live AutoCAD reachable: no running instance to attach to")
            return 2
        report = {"autocad": version, **await _smoke(backend, folder)}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        ok = (
            report["render"]["size"] == [600, 300]
            and report["render"]["labelled"] == 2
            and report["view_before"] == report["view_after"]
            and report["capture"] is not None
            and report["capture"]["width"] <= 800
        )
        return 0 if ok else 1
    finally:
        guards._STATE["expect"] = None
        await guards._close_leftovers(backend)
        await guards._reactivate_start(backend)
        guards._STATE["start"] = None
        await backend.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
