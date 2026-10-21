"""``drawing_digest``: the state of a drawing in a few hundred characters.

A screenshot after a series of edits costs ~500-1 850 tokens and still has to
be *read*; most of what it is taken for - did the objects land on the right
layers, is anything outside the frame, is a contour left open, is something
drawn twice - is a count. This module answers those counts from one snapshot
(``engineering/understand/snapshot.py``), and :func:`render_text` lays them
out as a dense plain-text report under a character budget.

Pure: a ``Snapshot`` in, a dict out; nothing touches a drawing.

What is reported
----------------
* **counts** - entities per layer and type, in the chosen space (model space
  by default), optionally narrowed to a window or to layers;
* **extents** - the computed box, the robust box and the outliers that
  stretch one into the other (``engineering.understand.units``);
* **text heights** - a histogram of TEXT / MTEXT heights;
* **checks**, each a count of entities plus at most :data:`HANDLES_PER_CHECK`
  of their handles:

  ``open_ends``          an end of a LINE / open polyline / ARC on a contour
                         layer that meets nothing (the ``drawing_topology_check``
                         engine). A contour layer is a layer with a continuous
                         linetype that is not a construction, dimension, text,
                         hatch, axis, level, leader, mark, frame or section-line layer by name
                         (English or Russian) - a centre line or a leader has
                         free ends by design;
  ``near_miss``          two such ends (or an end and a segment) closer than
                         the topology gap but not joined;
  ``crossing``           two straight segments of one contour layer crossing
                         away from their ends;
  ``not_closed``         an open polyline of three or more vertices whose ends
                         are within 1 % of its own size (or coincide): drawn as
                         a contour, not flagged closed - a hatch or an area
                         reads it as open;
  ``outside_frame``      an entity whose box leaves the frame: the ``frame``
                         given, else the largest axis-aligned closed rectangle
                         covering at least a quarter of the robust extents;
  ``duplicate``          an entity identical (type, layer, geometry, text) to
                         an earlier one;
  ``zero_size``          a LINE shorter than ``tol``, a CIRCLE / ARC with a
                         radius under ``tol``, a TEXT / MTEXT with no text;
  ``text_height``        a text height outside ``text_heights`` (only when the
                         caller gives the list - the right series depends on
                         the standard and the plot scale);
  ``construction_left``  anything on a ``CONSTRUCTION`` or ``_OUT`` layer;
  ``layer_0``            anything drawn on layer ``0``.

The contour-layer rule, the 1 % closure share and the quarter-extents frame
rule are this module's declared heuristics, not standards values.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace

from engineering.understand.diff import signature
from engineering.understand.snapshot import EntityRecord, Snapshot
from engineering.understand.topology import topology_findings
from engineering.understand.units import entity_box, robust_extents

__all__ = [
    "CHECKS",
    "DEFAULT_MAX_CHARS",
    "HANDLES_PER_CHECK",
    "contour_layers",
    "digest",
    "render_text",
]

DEFAULT_TOL = 0.01
DEFAULT_MAX_CHARS = 1500
HANDLES_PER_CHECK = 5
CHECKS = (
    "open_ends",
    "near_miss",
    "crossing",
    "not_closed",
    "outside_frame",
    "duplicate",
    "zero_size",
    "text_height",
    "construction_left",
    "layer_0",
)

#: Layer-name fragments that mark a layer as *not* a contour layer.
_NON_CONTOUR_WORDS = (
    "construct",
    "_out",
    "dim",
    "размер",
    "text",
    "текст",
    "hatch",
    "штрих",
    "axis",
    "center",
    "centre",
    "ось",
    "оси",
    "defpoints",
    "отметк",
    "level",
    "вынос",
    "leader",
    "марк",
    "рамк",
    "frame",
    "разрез",
    "section",
)
_CONTINUOUS = ("", "continuous", "bylayer", "byblock")
_NETWORK_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC"})
_TEXT_TYPES = frozenset({"TEXT", "MTEXT"})
_CONSTRUCTION_LAYERS = ("construction", "_out")


def _norm_space(snap: Snapshot, space: str | None) -> str:
    if space is None or space.strip().lower() in ("", "model", "modelspace", "*model_space"):
        return next((s for s in snap.layouts if s.lower() == "model"), "Model")
    wanted = space.strip().lower()
    for name in snap.layouts:
        if name.lower() == wanted:
            return name
    raise ValueError(f"no layout named {space!r}; layouts: {', '.join(snap.layouts)}")


def _inside(box, window) -> bool:
    x0, y0, x1, y1 = box
    wx0, wy0, wx1, wy1 = window
    return x1 >= wx0 and x0 <= wx1 and y1 >= wy0 and y0 <= wy1


def _select(
    snap: Snapshot, space: str, window: Sequence[float] | None, layers: Iterable[str] | None
) -> list[EntityRecord]:
    keep = {name.lower() for name in layers} if layers else None
    win = None
    if window is not None:
        x1, y1, x2, y2 = (float(v) for v in window)
        win = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    out = []
    for rec in snap.records:
        if rec.space != space:
            continue
        if keep is not None and rec.layer.lower() not in keep:
            continue
        if win is not None:
            box = entity_box(rec)
            if box is None or not _inside(box, win):
                continue
        out.append(rec)
    return out


def contour_layers(snap: Snapshot, records: Sequence[EntityRecord]) -> list[str]:
    """The layers among ``records`` whose free line ends mean something (module docstring)."""
    names = {rec.layer for rec in records if rec.type in _NETWORK_TYPES}
    out = []
    for name in sorted(names):
        low = name.lower()
        if any(word in low for word in _NON_CONTOUR_WORDS):
            continue
        linetype = str((snap.layers.get(name) or {}).get("linetype", "")).lower()
        if linetype not in _CONTINUOUS:
            continue
        out.append(name)
    return out


def _is_rectangle(rec: EntityRecord, tol: float) -> tuple[float, float, float, float] | None:
    if rec.type not in ("LWPOLYLINE", "POLYLINE") or any(abs(b) > 1e-9 for b in rec.bulges):
        return None
    pts = list(rec.points)
    if len(pts) == 5 and math.dist(pts[0], pts[-1]) <= tol:
        pts = pts[:4]
    elif not (rec.closed and len(pts) == 4):
        return None
    xs = sorted({round(p[0] / tol) for p in pts})
    ys = sorted({round(p[1] / tol) for p in pts})
    if len(xs) != 2 or len(ys) != 2:
        return None
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    return (x0, y0, x1, y1)


def _auto_frame(records: Sequence[EntityRecord], robust, tol: float):
    if robust is None:
        return None, None
    rx0, ry0, rx1, ry1 = robust
    floor = 0.25 * max(0.0, (rx1 - rx0) * (ry1 - ry0))
    best = None
    for rec in records:
        rect = _is_rectangle(rec, tol)
        if rect is None:
            continue
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if area >= floor and (best is None or area > best[0]):
            best = (area, rect, rec.handle)
    if best is None:
        return None, None
    return best[1], best[2]


def _check_rows() -> dict[str, list[str]]:
    return {name: [] for name in CHECKS}


def _height_key(h: float) -> float:
    return round(h, 3)


def digest(
    snap: Snapshot,
    *,
    space: str | None = None,
    window: Sequence[float] | None = None,
    layers: Iterable[str] | None = None,
    frame: Sequence[float] | None = None,
    text_heights: Iterable[float] | None = None,
    tol: float = DEFAULT_TOL,
) -> dict:
    """The digest of ``snap`` (see the module docstring for every key)."""
    if tol <= 0:
        raise ValueError(f"tol must be positive; got {tol!r}")
    space_name = _norm_space(snap, space)
    records = _select(snap, space_name, window, layers)

    counts: dict[str, Counter] = defaultdict(Counter)
    for rec in records:
        counts[rec.layer][rec.type] += 1
    by_layer = {
        layer: dict(sorted(types.items(), key=lambda kv: (-kv[1], kv[0])))
        for layer, types in sorted(counts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    }

    ext = robust_extents(records)
    heights = Counter(
        _height_key(rec.height)
        for rec in records
        if rec.type in _TEXT_TYPES and rec.height is not None and rec.height > 0
    )

    rows = _check_rows()
    counted: Counter = Counter()
    flagged: dict[str, set[str]] = defaultdict(set)

    def flag(check: str, handle: str) -> None:
        # counted per entity: a line with two free ends is one open entity
        if handle in flagged[check]:
            return
        flagged[check].add(handle)
        counted[check] += 1
        if len(rows[check]) < HANDLES_PER_CHECK:
            rows[check].append(handle)

    # topology on the contour layers
    contours = contour_layers(snap, records)
    topo_meta = None
    if contours:
        topo = topology_findings(replace(snap, records=tuple(records)), layers=contours, tol=tol)
        topo_meta = {"layers": contours, "gap": topo["gap"]}
        for row in topo["dangling"]:
            flag("open_ends", row["handle"])
        for row in topo["near_miss"]:
            flag("near_miss", row["handles"][0])
        for row in topo["crossing"]:
            flag("crossing", row["handles"][0])

    # frame
    frame_box = None
    frame_handle = None
    frame_source = None
    if frame is not None:
        x1, y1, x2, y2 = (float(v) for v in frame)
        frame_box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        frame_source = "given"
    else:
        frame_box, frame_handle = _auto_frame(records, ext.get("robust"), tol)
        frame_source = "largest_rectangle" if frame_box else None

    allowed = sorted({float(h) for h in text_heights}) if text_heights is not None else None
    seen: set[tuple] = set()
    for rec in records:
        layer_low = rec.layer.lower()
        if rec.type in ("LWPOLYLINE", "POLYLINE") and not rec.closed and len(rec.points) >= 3:
            box = entity_box(rec)
            size = math.hypot(box[2] - box[0], box[3] - box[1]) if box else 0.0
            if math.dist(rec.points[0], rec.points[-1]) <= max(tol, 0.01 * size):
                flag("not_closed", rec.handle)
        if frame_box is not None and rec.handle != frame_handle:
            box = entity_box(rec)
            if box is not None and (
                box[0] < frame_box[0] - tol
                or box[1] < frame_box[1] - tol
                or box[2] > frame_box[2] + tol
                or box[3] > frame_box[3] + tol
            ):
                flag("outside_frame", rec.handle)
        sig = signature(rec, tol)
        if sig in seen:
            flag("duplicate", rec.handle)
        else:
            seen.add(sig)
        if (
            (rec.type == "LINE" and len(rec.points) == 2 and math.dist(*rec.points) < tol)
            or (rec.type in ("CIRCLE", "ARC") and rec.radius is not None and rec.radius < tol)
            or (rec.type in _TEXT_TYPES and not (rec.text or "").strip())
        ):
            flag("zero_size", rec.handle)
        if (
            allowed is not None
            and rec.type in _TEXT_TYPES
            and rec.height is not None
            and not any(abs(rec.height - h) <= max(tol, 0.01 * h) for h in allowed)
        ):
            flag("text_height", rec.handle)
        if layer_low in _CONSTRUCTION_LAYERS:
            flag("construction_left", rec.handle)
        if rec.layer == "0":
            flag("layer_0", rec.handle)

    checks = {
        name: {"count": counted[name], "handles": rows[name]} for name in CHECKS if counted[name]
    }
    outliers = ext.get("outliers") or []
    return {
        "space": space_name,
        "window": list(window) if window is not None else None,
        "layers_filter": sorted(layers) if layers else None,
        "insunits": snap.insunits,
        "entities": len(records),
        "layer_count": len(by_layer),
        "by_layer": by_layer,
        "extents": {
            "computed": ext.get("computed"),
            "robust": ext.get("robust"),
            "outliers": [o["handle"] for o in outliers[:HANDLES_PER_CHECK]],
            "outlier_count": len(outliers),
        },
        "text_heights": {f"{h:g}": n for h, n in sorted(heights.items())},
        "frame": None
        if frame_box is None
        else {"box": list(frame_box), "source": frame_source, "handle": frame_handle},
        "contour_layers": topo_meta,
        "checks": checks,
        "layouts": list(snap.layouts),
        "ok": not checks,
    }


def _num(v: float) -> str:
    if v is None:
        return "?"
    r = round(float(v), 2)
    return f"{r:g}"


def _box(box) -> str:
    if not box:
        return "-"
    return f"{_num(box[0])},{_num(box[1])} .. {_num(box[2])},{_num(box[3])}"


def render_text(d: dict, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """``d`` as dense plain text, at most ``max_chars`` (the layer list is cut first)."""
    head = [
        f"DIGEST {d['space']}: {d['entities']} entities on {d['layer_count']} layers"
        + (f", window {_box(d['window'])}" if d.get("window") else "")
        + (f", layers {','.join(d['layers_filter'])}" if d.get("layers_filter") else ""),
    ]
    ext = d["extents"]
    line = f"EXTENTS {_box(ext['robust'])}"
    if ext["computed"] and ext["computed"] != ext["robust"]:
        line += f" (all {_box(ext['computed'])}; outliers {','.join(ext['outliers'])})"
    head.append(line)
    if d.get("frame"):
        fr = d["frame"]
        head.append(
            f"FRAME {_box(fr['box'])} ({fr['source']}"
            + (f" {fr['handle']}" if fr.get("handle") else "")
            + ")"
        )
    if d["text_heights"]:
        head.append("TEXT h " + " ".join(f"{h}x{n}" for h, n in d["text_heights"].items()))
    tail = []
    if d["checks"]:
        for name, row in d["checks"].items():
            more = row["count"] - len(row["handles"])
            tail.append(
                f"WARN {name} {row['count']}: {','.join(row['handles'])}"
                + (f" +{more}" if more > 0 else "")
            )
    else:
        tail.append("OK no warnings")
    layer_lines = [
        f" {layer} {sum(types.values())}: " + ", ".join(f"{t} {n}" for t, n in types.items())
        for layer, types in d["by_layer"].items()
    ]
    budget = max_chars - sum(len(s) + 1 for s in head + tail) - len("LAYERS\n")
    kept: list[str] = []
    for i, text in enumerate(layer_lines):
        rest = len(layer_lines) - i
        reserve = len(f" ... {rest} more layers") + 1 if rest > 1 else 0
        if budget - (len(text) + 1) < reserve and i < len(layer_lines) - 1:
            kept.append(f" ... {rest} more layers")
            break
        if budget - (len(text) + 1) < 0:
            kept.append(f" ... {rest} more layers")
            break
        kept.append(text)
        budget -= len(text) + 1
    out = "\n".join(head + (["LAYERS", *kept] if kept else []) + tail)
    return out if len(out) <= max_chars else out[: max_chars - 1] + "…"
