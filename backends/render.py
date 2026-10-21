"""Offscreen rendering of a drawing, and the post-processing every screenshot shares.

A screenshot is billed by its **pixels**, not its bytes: a client resizes an
image so its long side is at most 1568 px and charges about ``width × height
/ 750`` tokens. v1.6 rendered 1600 × 900, about 1 850 tokens a look. So the
levers are, in order:

1. **the frame** - render only the window that matters, in drawing
   coordinates, instead of the extents plus a margin;
2. **the width** - :data:`DEFAULT_WIDTH` 800 px is about 480 tokens at 16:9,
   and :data:`MIN_WIDTH` 200 px is enough to check that a part is where it
   should be;
3. **the encoding** - ``mono`` / ``gray`` and ``jpeg`` shrink the transfer
   and make thin lines legible on a small frame, but they do not change the
   token count.

:func:`render_doc` draws an ``ezdxf`` document (the headless engine's own, or
the live engine's ``Document.Export`` snapshot) with matplotlib on a white
sheet. :func:`postprocess` resizes and re-encodes any PNG - also a live window
capture, which cannot be re-rendered.

Pure and synchronous: callers run it on their own worker thread.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable, Sequence

log = logging.getLogger("autocad_mcp")

__all__ = [
    "DEFAULT_MAX_HANDLE_LABELS",
    "DEFAULT_WIDTH",
    "IMAGE_FORMATS",
    "MAX_WIDTH",
    "MIN_WIDTH",
    "SCREENSHOT_MODES",
    "image_tokens",
    "label_anchor",
    "new_agg_figure",
    "postprocess",
    "render_doc",
]

DEFAULT_WIDTH = 800
MIN_WIDTH = 200
#: A client downsizes anything wider; rendering more pixels than it keeps is waste.
MAX_WIDTH = 1568
DEFAULT_MAX_HANDLE_LABELS = 40

#: ``color`` as drawn; ``gray`` one channel; ``mono`` black ink on white.
SCREENSHOT_MODES = ("color", "gray", "mono")
IMAGE_FORMATS = ("png", "jpeg")

_DPI = 100


def image_tokens(width: int, height: int) -> int:
    """The approximate token cost of an image a client forwards to the model."""
    long_side = max(width, height)
    if long_side > MAX_WIDTH:
        scale = MAX_WIDTH / long_side
        width, height = int(width * scale), int(height * scale)
    return max(1, round(width * height / 750))


def new_agg_figure(*, figsize=None, dpi=_DPI):
    """Create a matplotlib Figure bound to the headless Agg canvas.

    Rendering runs inside an ``asyncio.to_thread`` worker. ``pyplot.figure``
    selects a *GUI* backend from global state (Qt/Tk), and instantiating a GUI
    canvas off the main thread can hard-crash the interpreter (SIGSEGV). Using
    ``Figure`` + ``FigureCanvasAgg`` directly bypasses pyplot's global backend
    selection entirely and is thread-safe; ``savefig`` still dispatches to the
    PDF backend by file extension when needed.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=figsize, dpi=dpi)
    FigureCanvasAgg(fig)  # binds fig.canvas to Agg (no pyplot, no GUI)
    return fig


def label_anchor(ent) -> tuple[float, float] | None:
    """Where an entity's handle label goes — the centre of its own extents.

    Uses the renderer's own bbox rather than the entity's stored coordinates, so
    the label lands where the entity is *drawn* even in a rotated frame (T0.4).
    """
    try:
        from ezdxf import bbox as _bbox

        box = _bbox.extents([ent])
        if not box.has_data:
            return None
        return (
            (float(box.extmin.x) + float(box.extmax.x)) / 2.0,
            (float(box.extmin.y) + float(box.extmax.y)) / 2.0,
        )
    except Exception as exc:
        log.debug("no label anchor for %s: %s", ent.dxftype(), exc)
        return None


def _resolve_layout(doc, space: str | None):
    """Model space, or the paper layout named ``space`` (case-insensitive)."""
    if space is None or space.strip().lower() in ("", "model", "modelspace", "*model_space"):
        return doc.modelspace()
    wanted = space.strip().lower()
    for name in doc.layouts.names():
        if name.lower() == wanted:
            return doc.layouts.get(name)
    raise ValueError(f"no layout named {space!r}; layouts: {', '.join(doc.layouts.names())}")


def wake_offscreen_viewports(doc) -> int:
    """Turn status -1 ("on, but off screen") viewports into drawn ones; return how many.

    For a snapshot only - it edits ``doc``. AutoCAD exports the viewports of
    a layout that is not on screen with status -1, and ezdxf's frontend draws
    only status > 0 (status 1 being the sheet itself), so a live render of a
    sheet showed the frame and nothing through its viewports (measured on
    AutoCAD 2024 / 24.3). A plot draws them, so the picture should too. The
    sheet's own viewport (id 1, else the first) becomes status 1; status 0
    (off) is left off.
    """
    woken = 0
    for layout in doc.layouts:
        if layout.is_modelspace:
            continue
        viewports = list(layout.query("VIEWPORT"))
        if not viewports:
            continue
        main = next((v for v in viewports if v.dxf.get("id") == 1), viewports[0])
        if main.dxf.get("status", 1) == -1:
            main.dxf.status = 1
        stack = max([int(v.dxf.get("status", 0)) for v in viewports] + [1])
        for viewport in viewports:
            if viewport is not main and viewport.dxf.get("status", 0) == -1:
                stack += 1
                viewport.dxf.status = stack
                woken += 1
    return woken


def _config(mode: str):
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration

    color = {
        # On a WHITE background ezdxf already resolves ACI 7 to black; a swap
        # policy on top would turn it white again (measured, ezdxf 1.4).
        "color": ColorPolicy.COLOR,
        "gray": ColorPolicy.MONOCHROME_LIGHT_BG,
        "mono": ColorPolicy.BLACK,
    }[mode]
    # min_lineweight: a line with the default weight renders thinner than a
    # pixel at screenshot sizes and all but vanishes (v1.6 drew it at about 5 %
    # contrast); 1.0 keeps every line at least one solid pixel.
    return Configuration(
        background_policy=BackgroundPolicy.WHITE, color_policy=color, min_lineweight=1.0
    )


def _in_window(point: Sequence[float], window: Sequence[float] | None) -> bool:
    if window is None:
        return True
    x1, y1, x2, y2 = window
    return min(x1, x2) <= point[0] <= max(x1, x2) and min(y1, y2) <= point[1] <= max(y1, y2)


def render_doc(
    doc,
    *,
    space: str | None = None,
    window: Sequence[float] | None = None,
    layers: Iterable[str] | None = None,
    mode: str = "color",
    width: int = DEFAULT_WIDTH,
    overlay_handles: bool = False,
    max_labels: int = DEFAULT_MAX_HANDLE_LABELS,
) -> dict:
    """Render ``doc`` to a PNG ``width`` pixels wide, framed to ``window`` or the extents.

    ``window`` is ``(x1, y1, x2, y2)`` in drawing units of the rendered space;
    the picture's aspect follows it, so no pixel is spent on a margin. ``layers``
    keeps only entities on those layers (case-insensitive). Returns ``{"png",
    "width", "height", "labels", "labelled", "total", "truncated"}``; ``labels``
    maps handle -> ``[x, y]`` for the entities labelled (those inside the window).
    """
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    if mode not in SCREENSHOT_MODES:
        raise ValueError(f"mode must be one of {', '.join(SCREENSHOT_MODES)}, got {mode!r}")
    width = max(MIN_WIDTH, min(MAX_WIDTH, int(width)))
    layout = _resolve_layout(doc, space)
    keep = {name.lower() for name in layers} if layers else None

    def _filter(entity) -> bool:
        return keep is None or str(entity.dxf.get("layer", "0")).lower() in keep

    fig = new_agg_figure(figsize=(16, 9), dpi=_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax), config=_config(mode)).draw_layout(
        layout, finalize=True, filter_func=_filter
    )
    if window is not None:
        x1, y1, x2, y2 = (float(v) for v in window)
        if x1 == x2 or y1 == y2:
            raise ValueError("window must have a non-zero width and height")
        ax.set_xlim(min(x1, x2), max(x1, x2))
        ax.set_ylim(min(y1, y2), max(y1, y2))
    xlo, xhi = ax.get_xlim()
    ylo, yhi = ax.get_ylim()
    aspect = (yhi - ylo) / (xhi - xlo) if xhi > xlo else 9 / 16
    # Never taller than wide by more than 2:1 - a sliver window would otherwise
    # ask for an image several thousand pixels high.
    aspect = max(0.05, min(2.0, aspect))
    height = max(1, round(width * aspect))
    fig.set_size_inches(width / _DPI, height / _DPI)

    labels: dict[str, list[float]] = {}
    entities = [e for e in layout if _filter(e)]
    total = len(entities)
    truncated = False
    if overlay_handles and total:
        limit = max(0, int(max_labels))
        placed = 0
        for ent in entities:
            anchor = label_anchor(ent)
            if anchor is None or not _in_window(anchor, window):
                continue
            if placed >= limit:
                truncated = True
                break
            handle = str(ent.dxf.get("handle", "?"))
            labels[handle] = [anchor[0], anchor[1]]
            placed += 1
            ax.annotate(
                handle,
                anchor,
                color="#d62728" if mode == "color" else "black",
                fontsize=7,
                ha="center",
                va="center",
                bbox={
                    "boxstyle": "round,pad=0.15",
                    "facecolor": "white",
                    "edgecolor": "#d62728" if mode == "color" else "black",
                    "linewidth": 0.4,
                    "alpha": 0.85,
                },
                annotation_clip=True,
            )
        if truncated:
            ax.text(
                0.01,
                0.99,
                f"handles shown for {len(labels)} of {total} entities",
                transform=ax.transAxes,
                color="#d62728" if mode == "color" else "black",
                fontsize=9,
                va="top",
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_DPI, facecolor="white")
    return {
        "png": buf.getvalue(),
        "width": width,
        "height": height,
        "labels": labels,
        "labelled": len(labels),
        "total": total,
        "truncated": truncated,
    }


def postprocess(
    png: bytes,
    *,
    width: int | None = DEFAULT_WIDTH,
    mode: str = "color",
    fmt: str = "png",
    quality: int = 70,
    dark_background: bool | None = None,
) -> tuple[bytes, dict]:
    """Resize (never enlarge) and re-encode a PNG. Returns ``(bytes, info)``.

    ``mono`` thresholds to black ink on white, inverting first when the
    background is dark (a live AutoCAD model space is), so the result reads the
    same as a headless render. ``dark_background`` None decides by the mean
    brightness - a drawing is mostly background, and a single corner pixel is
    not: a frame line through the corner fooled that test.
    """
    from PIL import Image, ImageOps

    if mode not in SCREENSHOT_MODES:
        raise ValueError(f"mode must be one of {', '.join(SCREENSHOT_MODES)}, got {mode!r}")
    if fmt not in IMAGE_FORMATS:
        raise ValueError(f"format must be one of {', '.join(IMAGE_FORMATS)}, got {fmt!r}")
    img = Image.open(io.BytesIO(png))
    img.load()
    if width is not None:
        target = max(MIN_WIDTH, min(MAX_WIDTH, int(width)))
        if img.width > target:
            img = img.resize(
                (target, max(1, round(img.height * target / img.width))), Image.LANCZOS
            )
    if mode in ("gray", "mono"):
        img = img.convert("L")
        if mode == "mono":
            if dark_background is None:
                from PIL import ImageStat

                dark_background = ImageStat.Stat(img).mean[0] < 128
            if dark_background:
                img = ImageOps.invert(img)
            # Anti-aliased thin lines sit well above black; keep them as ink.
            img = img.point(lambda v: 255 if v > 200 else 0, mode="1")
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    if fmt == "jpeg":
        if img.mode == "1":
            img = img.convert("L")
        img.save(out, format="JPEG", quality=max(10, min(95, int(quality))), optimize=True)
    else:
        img.save(out, format="PNG", optimize=True)
    data = out.getvalue()
    return data, {
        "width": img.width,
        "height": img.height,
        "format": fmt,
        "mode": mode,
        "bytes": len(data),
        "image_tokens": image_tokens(img.width, img.height),
    }
