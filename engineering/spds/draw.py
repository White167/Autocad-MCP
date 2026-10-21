"""СПДС primitives and the one place they become entities.

The sheet and mark modules are pure: they return these tuples, in millimetres
of paper, and a test asserts the tuples. :func:`draw_prims` scales them by the
drawing scale, moves them to the origin and draws them through the backend
contract, so both engines get the same entities.

Live ActiveX does not create a layer on assignment (``entity.Layer = "X"``
raises ``Key not found``), so :func:`ensure_resources` makes every linetype,
layer and the text style a primitive names *before* the first entity - the
repository rule for every draw path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from engineering.spds.standards import LAYERS, LINETYPES, TEXT_FONT, TEXT_STYLE

__all__ = [
    "Circ",
    "Fill",
    "Poly",
    "Seg",
    "Txt",
    "draw_prims",
    "ensure_resources",
    "text_width",
]

Pt = tuple[float, float]


class Seg(NamedTuple):
    p1: Pt
    p2: Pt
    layer: str


class Poly(NamedTuple):
    points: tuple[Pt, ...]
    closed: bool
    layer: str


class Circ(NamedTuple):
    center: Pt
    radius: float
    layer: str


class Txt(NamedTuple):
    at: Pt
    text: str
    height: float
    layer: str
    align: str = "L"
    width_factor: float = 1.0
    rotation: float = 0.0


class Fill(NamedTuple):
    """A solid-filled closed outline (a leader dot, an arrowhead)."""

    points: tuple[Pt, ...]
    layer: str


Prim = Seg | Poly | Circ | Txt | Fill

#: Advance widths as a share of the text height, from the ISO 3098 type B
#: letter (d = h/10): a capital 6d wide plus 2d spacing, a lowercase letter
#: 5d + 2d, a word space 6d, the broad letters (Ж Ш Щ М Ю Ф W) about 8d + 2d.
#: ISOCPEUR follows that letter. A headless engine has no font metrics, so a
#: text is fitted by this estimate and the result reports the width factor it
#: chose; a live seat may print slightly narrower, never wider.
_ADVANCE_CAPITAL = 0.8
_ADVANCE_LOWER = 0.7
_ADVANCE_SPACE = 0.6
_ADVANCE_BROAD = 1.0
_ADVANCE_NARROW = 0.4
_BROAD = frozenset("ЖШЩМЮФWжшщмюф")
_NARROW_CHARS = frozenset(".,:;!|'`()-iIl1")


def text_width(text: str, height: float, width_factor: float = 1.0) -> float:
    total = 0.0
    for ch in text:
        if ch == " ":
            total += _ADVANCE_SPACE
        elif ch in _BROAD:
            total += _ADVANCE_BROAD
        elif ch in _NARROW_CHARS:
            total += _ADVANCE_NARROW
        elif ch.isupper() or ch.isdigit() or ch == "№":
            total += _ADVANCE_CAPITAL
        else:
            total += _ADVANCE_LOWER
    return total * height * width_factor


def _layers_of(prims: Iterable[Prim]) -> list[str]:
    return sorted({p.layer for p in prims})


async def ensure_resources(backend, layers: Sequence[str], *, text_style: bool = True) -> dict:
    """Create the linetypes, layers and text style ``layers`` need; report what was made."""
    made = {"linetypes": [], "layers": [], "text_style": None}
    wanted_lt = {LAYERS[name][1] for name in layers if name in LAYERS}
    have_lt = {str(n).lower() for n in await backend.linetype_list()}
    for name in sorted(wanted_lt):
        if name.lower() in have_lt or name.lower() == "continuous":
            continue
        pattern, description = LINETYPES[name]
        await backend.linetype_define(name, list(pattern), description)
        made["linetypes"].append(name)
    have_layers = {str(getattr(info, "name", "")).lower() for info in await backend.layer_list()}
    for name in layers:
        if name.lower() in have_layers:
            continue
        color, linetype, lineweight = LAYERS.get(name, (7, "Continuous", 0.18))
        await backend.layer_create(name, color, linetype, lineweight)
        made["layers"].append(name)
    if text_style:
        styles = await backend.textstyle_list()
        names = {str(row.get("name", "")).lower() for row in styles}
        if TEXT_STYLE.lower() not in names:
            await backend.textstyle_create(TEXT_STYLE, TEXT_FONT)
            made["text_style"] = TEXT_STYLE
    return made


def _xy(p: Pt, scale: float, origin: Pt) -> list[float]:
    return [origin[0] + p[0] * scale, origin[1] + p[1] * scale]


async def draw_prims(
    backend,
    prims: Sequence[Prim],
    *,
    origin: Pt = (0.0, 0.0),
    scale: float = 1.0,
    record=None,
) -> list[str]:
    """Draw ``prims`` (mm of paper) at ``origin``, enlarged by ``scale``; return handles.

    ``record(prim)`` may return an ``ACADMCP_SPDS`` payload for a primitive;
    it is written onto that primitive's entity (``engineering/spds/records.py``).
    """
    await ensure_resources(
        backend, _layers_of(prims), text_style=any(isinstance(p, Txt) for p in prims)
    )
    handles: list[str] = []
    for prim in prims:
        if isinstance(prim, Seg):
            a, b = _xy(prim.p1, scale, origin), _xy(prim.p2, scale, origin)
            entity = await backend.entity_create_line(a[0], a[1], b[0], b[1], layer=prim.layer)
        elif isinstance(prim, Poly):
            entity = await backend.entity_create_polyline(
                points=[_xy(p, scale, origin) for p in prim.points],
                closed=prim.closed,
                layer=prim.layer,
            )
        elif isinstance(prim, Circ):
            c = _xy(prim.center, scale, origin)
            entity = await backend.entity_create_circle(
                c[0], c[1], prim.radius * scale, layer=prim.layer
            )
        elif isinstance(prim, Txt):
            at = _xy(prim.at, scale, origin)
            entity = await backend.entity_create_text(
                prim.text,
                at[0],
                at[1],
                prim.height * scale,
                prim.rotation,
                prim.layer,
                None,
                style=TEXT_STYLE,
                align=None if prim.align == "L" else prim.align,
                width_factor=None if prim.width_factor == 1.0 else prim.width_factor,
            )
        elif isinstance(prim, Fill):
            entity = await backend.entity_create_hatch(
                "SOLID", [_xy(p, scale, origin) for p in prim.points], layer=prim.layer
            )
        else:  # pragma: no cover - the modules only emit the five above
            raise ValueError(f"unknown СПДС primitive {type(prim).__name__}")
        handles.append(entity.handle)
        payload = record(prim) if record is not None else None
        if payload:
            from engineering.spds.records import write

            await write(backend, entity.handle, payload)
    return handles
