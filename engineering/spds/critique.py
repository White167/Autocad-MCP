"""The six СПДС critique focuses (stage 4 of the token-economy roadmap).

Every check is pure over one index built once per critique run and shared
through ``run_critique``'s context, the way the P&ID, mechanical and
architectural focuses are. The index is one :func:`read_document` call - the
live engine's ``Document.Export`` snapshot, or the headless document - read by
:func:`read_critique_index`: the ``ACADMCP_SPDS`` records of
``engineering/spds/records.py`` plus the layer table, the explicit lineweights
on СПДС layers and what stands outside each sheet's frame. A drawing with no
``ACADMCP_SPDS`` record yields no issue at all: an ISO sheet, a P&ID or a
foreign drawing that happens to have a layer called "Текст" is not judged by
ГОСТ.

* ``spds_text_height`` (warning): a text whose paper height (drawn height /
  scale) is not in ГОСТ 2.304's series (``TEXT_HEIGHTS``). A text with a
  record is divided by its own scale (a stamp text by its sheet's); an
  untagged text on an СПДС layer passes if any scale recorded in its layout
  (any at all when its layout has none) makes it a series height.
* ``spds_lineweight`` (warning): an СПДС layer whose weight differs from
  ``standards.LAYERS``, or an entity on an СПДС layer with its own weight
  outside ``GOST_LINEWEIGHTS`` (ByLayer / ByBlock / default always pass).
* ``spds_axis_letter`` (error): an axis mark with a letter ГОСТ Р 21.101
  forbids (Ё З Й О Х Ц Ч Щ Ъ Ы Ь), or two marks out of order - digits left
  to right, letters bottom up, by the ``dir`` / ``at`` of the axis records.
  Marks are compared per layout and direction; a label seen at several
  positions (two plans with the same grid) is out of order only when every
  position of the earlier label lies past every position of the later one.
  A label that is neither digits nor letters (``1/1``, ``А'``) is not ranked.
* ``spds_elevation_format`` (error): a level mark whose text is no longer
  ``format_elevation(value, decimals, signed)`` of its record - edited by
  hand into another format, or a value changed without the text. A level
  drawn from a ``text`` (no ``value``) is not judged.
* ``spds_stamp_incomplete`` (warning): graph 1, 2, 4, 6 or 7 of a sheet's
  stamp is empty in the texts the drawing now holds.
* ``spds_outside_frame`` (error): on a paper layout, an entity of the
  sheet's layout reaching past its frame; in model space only an entity that
  straddles the frame (geometry wholly outside a model-space sheet is other
  content of the model). The sheet's own lines and labels on the
  ``СПДС_Рамка`` layers are measured against the sheet's outer edge instead,
  and the layout's own paper viewport (id 1) is skipped.

The severities and the 0.5 mm-of-paper frame tolerance are this module's
decision, not a standard's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engineering.plan_spec import Issue
from engineering.spds.marks import format_elevation
from engineering.spds.records import read_index
from engineering.spds.standards import (
    AXIS_LETTERS,
    FORBIDDEN_AXIS_LETTERS,
    GOST_LINEWEIGHTS,
    LAYERS,
    TEXT_HEIGHTS,
    sheet_size,
)

if TYPE_CHECKING:
    from backends.base import AutoCADBackend

__all__ = [
    "REQUIRED_GRAPHS",
    "SPDS_DISPATCH",
    "SPDS_FOCUSES",
    "axis_rank",
    "build_index",
    "is_series_height",
    "issues_for",
    "read_critique_index",
]

SPDS_FOCUSES = (
    "spds_text_height",
    "spds_lineweight",
    "spds_axis_letter",
    "spds_elevation_format",
    "spds_stamp_incomplete",
    "spds_outside_frame",
)
_KEY = "spds_index"

#: Graphs of form 3 every sheet fills: шифр, объект, изображения, стадия, лист.
REQUIRED_GRAPHS: tuple[int, ...] = (1, 2, 4, 6, 7)
#: A paper height within this share of a series height is that height.
HEIGHT_TOL = 0.005
#: mm of paper an entity may reach past the frame before it is outside.
FRAME_TOL_MM = 0.5
_FRAME_LAYERS = frozenset({"спдс_рамка", "спдс_рамка_тонкие"})
_SPDS_LAYERS = {name.lower(): name for name in LAYERS}


# ── the index ───────────────────────────────────────────────────────────────


def _lineweight_mm(value) -> float | None:
    """DXF 1/100 mm to mm; ByLayer / ByBlock / default (negative) are None."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value / 100.0 if value >= 0 else None


def _box(entity):
    from ezdxf import bbox

    try:
        found = bbox.extents([entity], fast=True)
    except Exception:
        return None
    if not found.has_data:
        return None
    return [found.extmin.x, found.extmin.y, found.extmax.x, found.extmax.y]


def _outside(doc, sheets: list[dict]) -> list[dict]:
    """Entities past their sheet's frame (see the module docstring)."""
    by_layout: dict[str, list[dict]] = {}
    for sheet in sheets:
        if sheet.get("frame"):
            by_layout.setdefault(str(sheet.get("layout", "Model")), []).append(sheet)
    if not by_layout:
        return []
    rows: list[dict] = []
    for layout in doc.layouts:
        sheets_here = by_layout.get(layout.name)
        if not sheets_here:
            continue
        paper = layout.name != "Model"
        boxes = []
        for sheet in sheets_here:
            k = float(sheet.get("scale", 1.0) or 1.0)
            ox, oy = (sheet.get("origin") or (0.0, 0.0))[:2]
            try:
                w, h, _fmt, _orient = sheet_size(sheet["format"], sheet.get("orientation"))
            except (KeyError, ValueError):
                w = h = None
            outer = [ox, oy, ox + w * k, oy + h * k] if w is not None else sheet["frame"]
            boxes.append((sheet, [float(v) for v in sheet["frame"]], outer, FRAME_TOL_MM * k))
        for entity in layout:
            kind = entity.dxftype()
            if kind == "VIEWPORT" and int(entity.dxf.get("id", 0) or 0) == 1:
                continue
            box = _box(entity)
            if box is None:
                continue
            frame_layer = str(entity.dxf.layer).lower() in _FRAME_LAYERS
            inside_any = False
            straddles = None
            for sheet, frame, outer, tol in boxes:
                limit = outer if frame_layer else frame
                if (
                    box[0] >= limit[0] - tol
                    and box[1] >= limit[1] - tol
                    and box[2] <= limit[2] + tol
                    and box[3] <= limit[3] + tol
                ):
                    inside_any = True
                    break
                overlaps = (
                    box[0] < limit[2]
                    and box[2] > limit[0]
                    and box[1] < limit[3]
                    and box[3] > limit[1]
                )
                if overlaps and straddles is None:
                    straddles = sheet
            if inside_any:
                continue
            if paper:
                sheet = straddles or boxes[0][0]
            elif straddles is not None:
                sheet = straddles
            else:
                continue
            rows.append(
                {
                    "sheet": sheet.get("id"),
                    "layout": layout.name,
                    "handle": entity.dxf.handle,
                    "type": kind,
                    "layer": entity.dxf.layer,
                    "box": [round(v, 3) for v in box],
                    "frame": [float(v) for v in sheet["frame"]],
                }
            )
    return rows


def read_critique_index(doc) -> dict:
    """``read_index(doc)`` plus what the six focuses need from the same document.

    Adds ``layer_weights`` ({layer: mm | None}), ``weights`` (entities on an
    СПДС layer with a weight of their own) and ``outside`` (entities past a
    sheet's frame). A document with no record gets none of these read.
    """
    index = read_index(doc)
    index.update({"layer_weights": {}, "weights": [], "outside": []})
    if not has_spds_content(index):
        return index
    for layer in doc.layers:
        index["layer_weights"][layer.dxf.name] = _lineweight_mm(layer.dxf.get("lineweight", -3))
    for layout in doc.layouts:
        for entity in layout:
            if str(entity.dxf.get("layer", "0")).lower() not in _SPDS_LAYERS:
                continue
            weight = _lineweight_mm(entity.dxf.get("lineweight", -1))
            if weight is not None:
                index["weights"].append(
                    {
                        "handle": entity.dxf.handle,
                        "layer": entity.dxf.layer,
                        "layout": layout.name,
                        "lineweight": weight,
                    }
                )
    index["outside"] = _outside(doc, index["sheets"])
    return index


def _empty_index() -> dict:
    return {
        "sheets": [],
        "fields": [],
        "marks": [],
        "texts": [],
        "layer_weights": {},
        "weights": [],
        "outside": [],
    }


async def build_index(backend: AutoCADBackend) -> dict:
    """One read of the drawing for all six focuses.

    Every record this pack writes sits on an СПДС layer, so the layer table is
    read first: a drawing with none of them is never snapshotted (a live
    snapshot is a ``Document.Export``), which keeps ``focus=None`` cheap.
    """
    from engineering.understand.snapshot import read_document

    try:
        names = {str(getattr(layer, "name", "")).lower() for layer in await backend.layer_list()}
    except Exception:
        names = set(_SPDS_LAYERS)
    if not names & set(_SPDS_LAYERS):
        return _empty_index()
    return await read_document(backend, read_critique_index)


def has_spds_content(index: dict) -> bool:
    return any(index.get(bucket) for bucket in ("sheets", "fields", "marks"))


# ── rules ───────────────────────────────────────────────────────────────────


def is_series_height(paper_height: float) -> bool:
    return any(abs(paper_height - h) <= HEIGHT_TOL * h for h in TEXT_HEIGHTS)


def axis_rank(label: str) -> tuple[str, int] | None:
    """``("digit", n)`` / ``("letter", n)`` in marking order, or None (not ranked).

    Letters follow ``AXIS_LETTERS``, then the doubled ones (АА, ББ ...) as
    ``axis_letters`` hands them out.
    """
    text = str(label).strip()
    if text.isdigit():
        return ("digit", int(text))
    if text and len(set(text)) == 1 and text[0] in AXIS_LETTERS:
        return ("letter", (len(text) - 1) * len(AXIS_LETTERS) + AXIS_LETTERS.index(text[0]))
    return None


def _scale_of_field(index: dict, row: dict) -> float:
    for sheet in index["sheets"]:
        if sheet.get("id") == row.get("sheet"):
            return float(sheet.get("scale", 1.0) or 1.0)
    return 1.0


# ── the six checks ──────────────────────────────────────────────────────────


def _text_height(index: dict) -> list[Issue]:
    out: list[Issue] = []
    tagged: set[str] = set()
    for row in index["marks"] + index["fields"]:
        if "height" not in row:
            continue
        tagged.add(row["handle"])
        scale = (
            float(row.get("scale", 1.0) or 1.0)
            if row["kind"] == "mark"
            else _scale_of_field(index, row)
        )
        paper = row["height"] / scale
        if not is_series_height(paper):
            out.append(
                Issue(
                    "warning",
                    "spds_text_height",
                    f"Text {row.get('text', '')!r} is {paper:g} mm on paper (drawn "
                    f"{row['height']:g} at 1:{scale:g}); ГОСТ 2.304 heights are "
                    f"{', '.join(f'{h:g}' for h in TEXT_HEIGHTS)} mm.",
                    [row["handle"]],
                    {"paper_height": round(paper, 4), "scale": scale},
                )
            )
    scales_by_layout: dict[str, set[float]] = {}
    for row in index["marks"]:
        scales_by_layout.setdefault(row["layout"], set()).add(float(row.get("scale", 1.0) or 1.0))
    for row in index["sheets"]:
        scales_by_layout.setdefault(row["layout"], set()).add(float(row.get("scale", 1.0) or 1.0))
    every = set().union(*scales_by_layout.values()) if scales_by_layout else {1.0}
    for text in index["texts"]:
        if text["handle"] in tagged or text["tagged"]:
            continue
        if str(text["layer"]).lower() not in _SPDS_LAYERS:
            continue
        scales = scales_by_layout.get(text["layout"]) or every
        if any(is_series_height(text["height"] / s) for s in scales):
            continue
        out.append(
            Issue(
                "warning",
                "spds_text_height",
                f"A text on layer {text['layer']} is {text['height']:g} drawing units high: "
                f"at the scales recorded on {text['layout']} "
                f"({', '.join(f'1:{s:g}' for s in sorted(scales))}) that is no ГОСТ 2.304 height.",
                [text["handle"]],
                {"height": text["height"], "scales": sorted(scales)},
            )
        )
    return out


def _lineweight(index: dict) -> list[Issue]:
    out: list[Issue] = []
    for name, weight in sorted(index["layer_weights"].items()):
        standard = LAYERS.get(_SPDS_LAYERS.get(str(name).lower(), ""))
        if standard is None:
            continue
        if weight is None or abs(weight - standard[2]) > 1e-6:
            out.append(
                Issue(
                    "warning",
                    "spds_lineweight",
                    f"Layer {name} weighs "
                    f"{'Default' if weight is None else f'{weight:g} mm'}; the СПДС layer set "
                    f"gives it {standard[2]:g} mm (ГОСТ 2.303).",
                    [],
                    {"layer": name, "lineweight": weight, "expected": standard[2]},
                )
            )
    for row in index["weights"]:
        if any(abs(row["lineweight"] - w) <= 1e-6 for w in GOST_LINEWEIGHTS):
            continue
        out.append(
            Issue(
                "warning",
                "spds_lineweight",
                f"An entity on layer {row['layer']} has its own weight {row['lineweight']:g} mm, "
                f"outside ГОСТ 2.303's range ({', '.join(f'{w:g}' for w in GOST_LINEWEIGHTS)}); "
                "set it ByLayer.",
                [row["handle"]],
                {"layer": row["layer"], "lineweight": row["lineweight"]},
            )
        )
    return out


def _axis_letter(index: dict) -> list[Issue]:
    out: list[Issue] = []
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    for row in index["marks"]:
        if row.get("mark") != "axis" or not row.get("label"):
            continue
        label = str(row["label"]).strip()
        bad = sorted(set(label) & FORBIDDEN_AXIS_LETTERS)
        if bad:
            out.append(
                Issue(
                    "error",
                    "spds_axis_letter",
                    f"Axis mark {label!r} uses {', '.join(bad)}, which ГОСТ Р 21.101 forbids "
                    "for axes (Ё З Й О Х Ц Ч Щ Ъ Ы Ь).",
                    [row["handle"]],
                    {"label": label, "forbidden": bad},
                )
            )
        if row.get("at") is None or row.get("dir") not in ("x", "y"):
            continue
        entry = groups.setdefault((row["layout"], row["dir"]), {}).setdefault(
            label, {"at": [], "handles": []}
        )
        entry["at"].append(float(row["at"]))
        entry["handles"].append(row["handle"])
    for (layout, direction), labels in sorted(groups.items()):
        ranked = [(axis_rank(lbl), lbl, e) for lbl, e in labels.items() if axis_rank(lbl)]
        ranked.sort(key=lambda item: item[0])
        way = "left to right" if direction == "x" else "bottom up"
        for i, (rank_a, a, ea) in enumerate(ranked):
            for rank_b, b, eb in ranked[i + 1 :]:
                if rank_a[0] != rank_b[0] or rank_a == rank_b:
                    continue
                if min(ea["at"]) > max(eb["at"]) + 1e-6:
                    out.append(
                        Issue(
                            "error",
                            "spds_axis_letter",
                            f"Axis {a} stands past axis {b} on {layout}: marks run {way} "
                            "(ГОСТ Р 21.101).",
                            ea["handles"] + eb["handles"],
                            {"labels": [a, b], "at": [min(ea["at"]), max(eb["at"])]},
                        )
                    )
    return out


def _elevation_format(index: dict) -> list[Issue]:
    out: list[Issue] = []
    for row in index["marks"]:
        if row.get("mark") != "elevation" or row.get("value") is None or "text" not in row:
            continue
        try:
            expected = format_elevation(
                float(row["value"]), int(row.get("decimals", 3)), bool(row.get("signed", True))
            )
        except (TypeError, ValueError):
            continue
        actual = str(row["text"]).strip()
        if actual != expected:
            out.append(
                Issue(
                    "error",
                    "spds_elevation_format",
                    f"Level mark reads {actual!r} but its record says {expected!r} "
                    "(metres, decimal comma, zero unsigned - ГОСТ Р 21.101 п. 5.4.3); "
                    "redraw it with spds_elevation.",
                    [row["handle"]],
                    {"text": actual, "expected": expected, "value": row["value"]},
                )
            )
    return out


def _stamp_incomplete(index: dict) -> list[Issue]:
    out: list[Issue] = []
    for sheet in index["sheets"]:
        filled = {
            int(row.get("graph", 0))
            for row in index["fields"]
            if row.get("sheet") == sheet.get("id") and str(row.get("text", "")).strip()
        }
        missing = [g for g in REQUIRED_GRAPHS if g not in filled]
        if missing:
            out.append(
                Issue(
                    "warning",
                    "spds_stamp_incomplete",
                    f"The stamp of sheet {sheet.get('id')} on {sheet.get('layout')} has graph(s) "
                    f"{', '.join(map(str, missing))} empty; fill them with spds_sheet_update.",
                    [sheet["handle"]],
                    {"sheet": sheet.get("id"), "missing": missing},
                )
            )
    return out


def _outside_frame(index: dict) -> list[Issue]:
    return [
        Issue(
            "error",
            "spds_outside_frame",
            f"{row['type']} on layer {row['layer']} ({row['layout']}) reaches past the frame "
            f"of sheet {row['sheet']}: box {row['box']}, frame {row['frame']}.",
            [row["handle"]],
            {k: row[k] for k in ("sheet", "layout", "box", "frame")},
        )
        for row in index["outside"]
    ]


_CHECKS = {
    "spds_text_height": _text_height,
    "spds_lineweight": _lineweight,
    "spds_axis_letter": _axis_letter,
    "spds_elevation_format": _elevation_format,
    "spds_stamp_incomplete": _stamp_incomplete,
    "spds_outside_frame": _outside_frame,
}


def issues_for(focus: str, index: dict) -> list[Issue]:
    if not has_spds_content(index):
        return []
    return _CHECKS[focus](index)


def _make(focus: str):
    async def check(backend: AutoCADBackend, shared: dict) -> list[Issue]:
        index = shared.get(_KEY)
        if index is None:
            index = shared[_KEY] = await build_index(backend)
        return issues_for(focus, index)

    check.needs_shared = True  # type: ignore[attr-defined]
    check.__name__ = f"check_{focus}"
    return check


SPDS_DISPATCH = {focus: _make(focus) for focus in SPDS_FOCUSES}
