# tests/test_screenshot_options.py
"""Cheap screenshots (v1.7): framed, small, mono - billed by pixels, not bytes.

`view_screenshot` renders on a white sheet at 800 px by default (1600 before),
frames to a window in drawing units, filters layers, and re-encodes as
gray / mono / JPEG. On the live engine a framed picture is an offscreen render
of a `Document.Export` snapshot, so the operator's view never moves.
"""

from __future__ import annotations

import base64
import io

import ezdxf
import pytest
from fastmcp import Client
from PIL import Image

import server
from backends.render import (
    DEFAULT_WIDTH,
    MAX_WIDTH,
    image_tokens,
    postprocess,
    render_doc,
)

pytestmark = pytest.mark.asyncio


def _doc():
    doc = ezdxf.new()
    doc.layers.add("A", color=7)
    doc.layers.add("B", color=7)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "A"})
    msp.add_circle((80, 40), 10, dxfattribs={"layer": "B"})
    return doc


def _img(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def _ink(img: Image.Image, box=None) -> int:
    """Dark pixels, in the whole image or in a (left, top, right, bottom) box."""
    gray = img.convert("L")
    if box is not None:
        gray = gray.crop(box)
    return sum(1 for v in gray.getdata() if v < 128)


# ── the pure renderer ───────────────────────────────────────────────────────


def test_a_render_is_black_ink_on_white_at_the_requested_width():
    out = render_doc(_doc(), width=600)
    img = _img(out["png"])
    assert img.width == 600 and out["width"] == 600
    assert img.convert("L").getpixel((0, 0)) == 255, "a white sheet, not model space's dark"
    assert _ink(img) > 100, "ACI 7 is drawn black and at least a pixel wide"


def test_the_window_frames_the_render_and_sets_its_aspect():
    out = render_doc(_doc(), window=(60, 20, 100, 60), width=400)
    assert (out["width"], out["height"]) == (400, 400), "a square window, a square image"
    img = _img(out["png"])
    # the circle (80, 40) r10 fills the middle; the line at y=0 is outside the frame
    assert _ink(img) > 100
    assert _ink(img, (0, 390, 400, 400)) == 0


def test_a_layer_filter_leaves_the_other_layers_out():
    full = _img(render_doc(_doc(), window=(60, 20, 100, 60), width=300)["png"])
    only_a = _img(render_doc(_doc(), window=(60, 20, 100, 60), width=300, layers=["a"])["png"])
    assert _ink(full) > 50 and _ink(only_a) == 0, "layer B's circle is filtered (case-insensitive)"


def test_labels_are_drawn_only_inside_the_window():
    doc = _doc()
    out = render_doc(doc, window=(60, 20, 100, 60), overlay_handles=True)
    circle = next(e for e in doc.modelspace() if e.dxftype() == "CIRCLE")
    assert set(out["labels"]) == {circle.dxf.handle}


def test_a_degenerate_window_and_an_unknown_layout_are_refused():
    with pytest.raises(ValueError, match="non-zero"):
        render_doc(_doc(), window=(0, 0, 0, 10))
    with pytest.raises(ValueError, match="no layout named 'Sheet9'"):
        render_doc(_doc(), space="Sheet9")


def test_postprocess_never_enlarges_and_caps_at_the_client_limit():
    png = render_doc(_doc(), width=300)["png"]
    _data, info = postprocess(png, width=1200)
    assert info["width"] == 300
    big = Image.new("RGB", (3000, 1000), "white")
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    _data, info = postprocess(buf.getvalue(), width=5000)
    assert info["width"] == MAX_WIDTH


def test_mono_inverts_a_dark_capture_even_with_a_line_through_the_corner():
    """A live model space is dark; a frame line through the corner pixel used to
    decide the inversion. The mean brightness decides now."""
    img = Image.new("RGB", (400, 300), (33, 40, 48))
    for x in range(400):
        img.putpixel((x, 0), (255, 255, 255))  # a light line along the top edge
    for y in range(300):
        img.putpixel((0, y), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data, info = postprocess(buf.getvalue(), mode="mono", width=None)
    out = _img(data).convert("L")
    assert info["mode"] == "mono"
    assert out.getpixel((200, 150)) == 255, "the dark background became white"
    assert out.getpixel((200, 0)) == 0, "the light line became ink"


def test_jpeg_and_gray_encode_and_report_the_image_cost():
    png = render_doc(_doc(), width=800)["png"]
    data, info = postprocess(png, mode="gray", fmt="jpeg", quality=60)
    assert data[:2] == b"\xff\xd8"
    assert info["format"] == "jpeg" and _img(data).mode == "L"
    assert info["image_tokens"] == image_tokens(info["width"], info["height"])
    assert image_tokens(1600, 900) > 3 * image_tokens(800, 450)


# ── the tools ───────────────────────────────────────────────────────────────


async def _shot(client, tool="view_screenshot", **args):
    result = await client.call_tool(tool, args)
    block = result.content[0]
    return _img(base64.b64decode(block.data)), block.mimeType


async def _scene(client):
    await client.call_tool("drawing_new", {})
    await client.call_tool("entity_create_rectangle", {"x1": 0, "y1": 0, "x2": 420, "y2": 297})
    await client.call_tool("entity_create_circle", {"cx": 50, "cy": 50, "radius": 20})


async def test_the_default_screenshot_is_800_px_wide():
    async with Client(server.mcp) as client:
        await _scene(client)
        img, mime = await _shot(client)
    assert img.width == DEFAULT_WIDTH and mime == "image/png"


async def test_a_framed_mono_jpeg_screenshot():
    async with Client(server.mcp) as client:
        await _scene(client)
        img, mime = await _shot(
            client, window=[20, 20, 80, 80], width=300, mode="mono", format="jpeg"
        )
    assert mime == "image/jpeg"
    assert img.size == (300, 300)


async def test_the_window_needs_four_numbers():
    async with Client(server.mcp) as client:
        await _scene(client)
        bad = await client.call_tool(
            "view_screenshot", {"window": [0, 0, 10]}, raise_on_error=False
        )
        partial = await client.call_tool(
            "view_zoom_and_screenshot", {"x1": 0, "y1": 0}, raise_on_error=False
        )
    assert bad.is_error
    assert partial.is_error and "all four" in partial.content[0].text


async def test_zoom_and_screenshot_frames_the_render_headlessly():
    """v1.6 ignored the window headlessly (there is no view to zoom) and
    returned the extents; the window now frames the render."""
    async with Client(server.mcp) as client:
        await _scene(client)
        img, _ = await _shot(
            client, "view_zoom_and_screenshot", x1=20, y1=20, x2=80, y2=50, width=400
        )
    assert img.size == (400, 200)


async def test_render_window_on_the_headless_engine_renders():
    async with Client(server.mcp) as client:
        await _scene(client)
        img, _ = await _shot(client, render="window", width=320)
    assert img.width == 320


# ── the live engine, without AutoCAD ────────────────────────────────────────


async def test_the_live_engine_renders_its_export_snapshot(monkeypatch, tmp_path):
    """ComBackend.view_render exports the active document (the measured
    drawing_understand route), renders the DXF and deletes the folder - the
    AutoCAD view is never touched."""
    from backends import com_backend
    from engineering.understand import snapshot

    folder = tmp_path / "snap"
    folder.mkdir()
    path = folder / "snap.DXF"
    _doc().saveas(path)
    monkeypatch.setattr(
        snapshot, "_com_export", lambda backend, p: (str(path), str(folder), "live:x")
    )

    class _Fake:
        async def _run(self, func, *args):
            return func(*args)

    out = await com_backend.ComBackend.view_render(
        _Fake(), window=(60, 20, 100, 60), width=200, overlay_handles=True
    )
    assert (out["width"], out["height"]) == (200, 200)
    assert out["labelled"] == 1
    assert not folder.exists(), "the snapshot folder is removed"
