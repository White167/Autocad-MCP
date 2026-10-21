"""СПДС marks: coordinate axes, the axis grid, level marks, leaders.

Pure, in millimetres of paper: a mark is drawn at the size it has on the
printed sheet, and ``draw.draw_prims(scale=M)`` enlarges it for a model drawn
1:1 and plotted at 1:M - the same rule as the skill's ``k`` multiplier. A
coordinate passed *in* (an axis end, a level point, a leader target) is a
drawing coordinate and is divided by the scale here, so the caller always
speaks drawing units.

Every rule cited is in ``engineering/spds/standards.py``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from engineering.spds.draw import Circ, Fill, Poly, Seg, Txt, text_width
from engineering.spds.standards import (
    AXIS_CIRCLE_RANGE,
    AXIS_LETTERS,
    AXIS_MARK_DIAMETER,
    AXIS_MARK_HEIGHT,
    ELEVATION,
)

__all__ = [
    "axis_grid_marks",
    "axis_grid_prims",
    "axis_letters",
    "axis_prims",
    "elevation_prims",
    "format_elevation",
    "leader_prims",
]

AXIS_LAYER = "Оси"
AXIS_MARK_LAYER = "Оси_марки"
TEXT_LAYER = "Текст"
LEVEL_LAYER = "Отметки"
LEADER_LAYER = "Выноски"

Pt = tuple[float, float]


# ── axes ────────────────────────────────────────────────────────────────────


def axis_letters(count: int) -> list[str]:
    """``count`` letter marks: А Б В ... without Ё З Й О Х Ц Ч Щ Ъ Ы Ь, then АА ББ ..."""
    out = []
    n = len(AXIS_LETTERS)
    for i in range(count):
        letter = AXIS_LETTERS[i % n]
        out.append(letter * (i // n + 1))
    return out


def _check_diameter(d: float) -> float:
    lo, hi = AXIS_CIRCLE_RANGE
    if not lo <= d <= hi:
        raise ValueError(f"an axis mark circle is {lo:g}-{hi:g} mm (ГОСТ Р 21.101); got {d:g}")
    return float(d)


def axis_prims(
    p1: Pt,
    p2: Pt,
    mark: str,
    *,
    ends: str = "start",
    diameter: float = AXIS_MARK_DIAMETER,
    scale: float = 1.0,
) -> list:
    """One axis from ``p1`` to ``p2`` (drawing units) with its mark in a circle.

    ``ends``: ``start`` (the circle beyond ``p1``), ``end``, ``both`` or
    ``none``. The circle touches the line's end and sits on its extension.
    """
    d = _check_diameter(diameter)
    if ends not in ("start", "end", "both", "none"):
        raise ValueError("ends is one of start, end, both, none")
    a = (p1[0] / scale, p1[1] / scale)
    b = (p2[0] / scale, p2[1] / scale)
    length = math.dist(a, b)
    if length <= 0:
        raise ValueError("an axis needs two different points")
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    prims: list = [Seg(a, b, AXIS_LAYER)]
    r = d / 2.0
    for which in ("start", "end"):
        if ends not in (which, "both"):
            continue
        if which == "start":
            c = (a[0] - ux * r, a[1] - uy * r)
        else:
            c = (b[0] + ux * r, b[1] + uy * r)
        prims.append(Circ(c, r, AXIS_MARK_LAYER))
        height = AXIS_MARK_HEIGHT if len(mark) <= 2 else AXIS_MARK_HEIGHT * 0.7
        factor = min(1.0, (d - 1.5) / max(text_width(mark, height), 1e-9))
        prims.append(Txt(c, mark, height, TEXT_LAYER, "MC", round(factor, 3)))
    return prims


def axis_grid_marks(n_vertical: int, n_horizontal: int) -> tuple[list[str], list[str], str]:
    """Marks for ``n_vertical`` axes (left to right) and ``n_horizontal`` (bottom up).

    Digits go to the side with more axes, letters to the other (ties: digits
    on the vertical axes, read left to right as a plan is). Returns (vertical
    marks, horizontal marks, which side got the digits).
    """
    digits_on_vertical = n_vertical >= n_horizontal
    if digits_on_vertical:
        return (
            [str(i + 1) for i in range(n_vertical)],
            axis_letters(n_horizontal),
            "vertical",
        )
    return axis_letters(n_vertical), [str(i + 1) for i in range(n_horizontal)], "horizontal"


# ── levels ──────────────────────────────────────────────────────────────────


def format_elevation(value: float, decimals: int = 3, signed: bool = True) -> str:
    """ГОСТ Р 21.101 п. 5.4.3: metres, ``decimals`` places, decimal comma; the
    zero level unsigned, ``+`` above, ``-`` below (``signed=False``: only the
    minus, for absolute levels such as ``160,65``)."""
    if decimals < 0 or decimals > 4:
        raise ValueError("decimals is 0-4")
    rounded = round(float(value), decimals)
    text = f"{abs(rounded):.{decimals}f}".replace(".", ",")
    if rounded == 0:
        return text
    if rounded < 0:
        return "-" + text
    return ("+" + text) if signed else text


def elevation_prims(
    at: Pt,
    text: str,
    *,
    kind: str = "section",
    direction: int = 1,
    scale: float = 1.0,
) -> list:
    """A level mark at ``at`` (drawing units).

    ``section`` (разрезы, фасады): two thin 45° strokes from the level point,
    a stem, a shelf to ``direction`` (1 right, -1 left), the text above the
    shelf. ``plan``: the text in a rectangle centred on ``at``.
    """
    if direction not in (1, -1):
        raise ValueError("direction is 1 (shelf to the right) or -1 (to the left)")
    x, y = at[0] / scale, at[1] / scale
    h = ELEVATION["text_height"]
    width = text_width(text, h)
    if kind == "plan":
        pad = ELEVATION["plan_padding"]
        half_w, half_h = width / 2 + pad, h / 2 + pad
        rect = (
            (x - half_w, y - half_h),
            (x + half_w, y - half_h),
            (x + half_w, y + half_h),
            (x - half_w, y + half_h),
        )
        return [Poly(rect, True, LEVEL_LAYER), Txt((x, y), text, h, LEVEL_LAYER, "MC")]
    if kind != "section":
        raise ValueError("kind is section or plan")
    s = ELEVATION["stroke"]
    top = y + ELEVATION["stem"]
    shelf = ELEVATION["shelf_extra"] + width
    prims: list = [
        Seg((x, y), (x - s, y + s), LEVEL_LAYER),
        Seg((x, y), (x + s, y + s), LEVEL_LAYER),
        Seg((x, y), (x, top), LEVEL_LAYER),
        Seg((x, top), (x + direction * shelf, top), LEVEL_LAYER),
    ]
    inset = ELEVATION["text_inset"]
    tx = x + inset if direction > 0 else x - shelf + inset
    prims.append(Txt((tx, top + ELEVATION["text_rise"]), text, h, LEVEL_LAYER, "L"))
    return prims


# ── leaders ─────────────────────────────────────────────────────────────────


def leader_prims(
    points: Sequence[Pt],
    text: str,
    *,
    text_below: str | None = None,
    end: str = "dot",
    height: float = 2.5,
    scale: float = 1.0,
) -> list:
    """A leader: from the target ``points[0]`` through the others to a shelf.

    The shelf runs from the last point away from the one before it, as long as
    the longer text plus 1 mm; ``text`` stands 1 mm above it, ``text_below``
    1 mm under. ``end`` at the target: ``dot`` (a filled 1.5 mm dot - the
    leader ends inside a contour), ``arrow`` (on a contour line), ``none``.
    """
    if len(points) < 2:
        raise ValueError("a leader needs at least two points: the target and the shelf start")
    if end not in ("dot", "arrow", "none"):
        raise ValueError("end is dot, arrow or none")
    pts = [(p[0] / scale, p[1] / scale) for p in points]
    prims: list = [Poly(tuple(pts), False, LEADER_LAYER)]
    last, before = pts[-1], pts[-2]
    direction = 1 if last[0] >= before[0] else -1
    width = max(text_width(text, height), text_width(text_below or "", height)) + 1.0
    shelf_end = (last[0] + direction * width, last[1])
    prims.append(Seg(last, shelf_end, LEADER_LAYER))
    left = min(last[0], shelf_end[0]) + 0.5
    prims.append(Txt((left, last[1] + 1.0), text, height, TEXT_LAYER, "BL"))
    if text_below:
        prims.append(Txt((left, last[1] - 1.0), text_below, height, TEXT_LAYER, "TL"))
    target, nxt = pts[0], pts[1]
    if end == "dot":
        r = 0.75
        ring = tuple(
            (
                target[0] + r * math.cos(2 * math.pi * k / 12),
                target[1] + r * math.sin(2 * math.pi * k / 12),
            )
            for k in range(12)
        )
        prims.append(Fill(ring, LEADER_LAYER))
    elif end == "arrow":
        length, half = 3.0, 0.5
        dx, dy = nxt[0] - target[0], nxt[1] - target[1]
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        base = (target[0] + ux * length, target[1] + uy * length)
        prims.append(
            Fill(
                (
                    target,
                    (base[0] - uy * half, base[1] + ux * half),
                    (base[0] + uy * half, base[1] - ux * half),
                ),
                LEADER_LAYER,
            )
        )
    return prims


def axis_grid_prims(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    overhang: float = 10.0,
    marks: str = "bottom_left",
    diameter: float = AXIS_MARK_DIAMETER,
    scale: float = 1.0,
) -> dict:
    """A grid of coordinate axes: vertical axes at ``xs``, horizontal at ``ys``.

    Each axis runs ``overhang`` mm of paper past the outermost crossing axes,
    and is marked at the bottom / left ends (``marks="bottom_left"``, the rule
    for a plan) or at both ends (``"all"``). Marks follow
    :func:`axis_grid_marks`. Returns ``{"prims", "vertical", "horizontal",
    "digits_on"}`` - the marks in drawing order.
    """
    if marks not in ("bottom_left", "all"):
        raise ValueError("marks is bottom_left or all")
    xs = sorted(float(x) for x in xs)
    ys = sorted(float(y) for y in ys)
    if not xs or not ys:
        raise ValueError("a grid needs at least one vertical (xs) and one horizontal (ys) axis")
    if len(set(xs)) != len(xs) or len(set(ys)) != len(ys):
        raise ValueError("two axes of a grid share a position")
    v_marks, h_marks, digits_on = axis_grid_marks(len(xs), len(ys))
    over = overhang * scale
    y0, y1 = ys[0] - over, ys[-1] + over
    x0, x1 = xs[0] - over, xs[-1] + over
    ends = "start" if marks == "bottom_left" else "both"
    prims: list = []
    for x, mark in zip(xs, v_marks, strict=True):
        prims.extend(axis_prims((x, y0), (x, y1), mark, ends=ends, diameter=diameter, scale=scale))
    for y, mark in zip(ys, h_marks, strict=True):
        prims.extend(axis_prims((x0, y), (x1, y), mark, ends=ends, diameter=diameter, scale=scale))
    return {
        "prims": prims,
        "vertical": dict(zip(v_marks, xs, strict=True)),
        "horizontal": dict(zip(h_marks, ys, strict=True)),
        "digits_on": digits_on,
    }
