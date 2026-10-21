"""The СПДС sheet: outer sheet line, frame 20/5/5/5, основная надпись форма 3.

Pure: :func:`sheet_prims` returns primitives in millimetres of paper with the
sheet's lower-left corner at (0, 0); ``engineering/spds/draw.py`` places them.
Geometry and text positions are ``standards.FORM3`` (transcribed from the
owner's verified ``spds:form3`` / ``spds:frame``).

Text fitting follows the skill's ``spds:tf`` / ``spds:tf2``: a text wider than
its cell is compressed (width factor) down to 0.8; a wrapping field still too
wide is split at the space nearest its middle into two lines 3 mm above and
below the cell centre; a field that cannot split falls back to 2.5 mm. Widths
are the ISOCPEUR estimate of ``draw.text_width`` (there are no font metrics
headlessly), and every fitted field is reported with the factor it got.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from engineering.spds.draw import Seg, Txt, text_width
from engineering.spds.standards import (
    EXTRA_COLUMNS,
    FORM3,
    FORM3_FIELD_NAMES,
    FORMAT_LABEL,
    FRAME_MARGINS,
    sheet_size,
)

__all__ = ["MIN_WIDTH_FACTOR", "normalise_fields", "sheet_prims"]

MIN_WIDTH_FACTOR = 0.8
THICK = "СПДС_Рамка"
THIN = "СПДС_Рамка_тонкие"


def normalise_fields(fields: Mapping | None) -> dict[int, str]:
    """``fields`` keyed by graph number (1-9) or name (`code`, `project`, ...)."""
    out: dict[int, str] = {}
    for key, value in (fields or {}).items():
        if value is None or str(value).strip() == "":
            continue
        if isinstance(key, int) or str(key).strip().isdigit():
            graph = int(key)
        else:
            name = str(key).strip().lower()
            if name not in FORM3_FIELD_NAMES:
                raise ValueError(
                    f"field {key!r} is not a graph of form 3; use 1-9 or "
                    f"{', '.join(FORM3_FIELD_NAMES)}"
                )
            graph = FORM3_FIELD_NAMES[name]
        if graph not in FORM3["fields"]:
            raise ValueError(
                f"graph {graph} has no cell in this form 3 (have {sorted(FORM3['fields'])})"
            )
        out[graph] = str(value).strip()
    return out


def _split(text: str) -> tuple[str, str] | None:
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return None
    middle = len(text) / 2
    best = min(spaces, key=lambda i: abs(i - middle))
    return text[:best], text[best + 1 :]


def _fit_line(text: str, at, height: float, max_width: float, align: str, fitted: list, key):
    width = text_width(text, height)
    factor = 1.0 if width <= max_width else max_width / width
    fitted.append({"field": key, "text": text, "height": height, "width_factor": round(factor, 3)})
    return Txt(at, text, height, THICK, align, round(factor, 3))


def _fit(text: str, cx: float, cy: float, height: float, max_width: float, wrap: bool, fitted, key):
    if text_width(text, height) * MIN_WIDTH_FACTOR <= max_width or not wrap:
        return [_fit_line(text, (cx, cy), height, max_width, "MC", fitted, key)]
    parts = _split(text)
    if parts is None:
        return [_fit_line(text, (cx, cy), 2.5, max_width, "MC", fitted, key)]
    return [
        _fit_line(parts[0], (cx, cy + 3.0), height, max_width, "MC", fitted, key),
        _fit_line(parts[1], (cx, cy - 3.0), height, max_width, "MC", fitted, key),
    ]


def sheet_prims(
    fmt: str = "A3",
    orientation: str | None = None,
    *,
    fields: Mapping | None = None,
    signatures: Sequence[Sequence[str]] = (),
    extra_columns: bool = False,
) -> dict:
    """The sheet's primitives and the facts about them.

    ``signatures`` is up to five ``(role, surname, date)`` rows, top first
    (Разраб., Пров., Н. контр., Утв. ...). Returns ``{"prims", "size",
    "format", "orientation", "frame", "stamp_origin", "fitted"}``.
    """
    width, height, key, orient = sheet_size(fmt, orientation)
    graphs = normalise_fields(fields)
    rows = list(signatures or ())
    if len(rows) > FORM3["signature_rows"]:
        raise ValueError(f"form 3 has {FORM3['signature_rows']} signature rows; got {len(rows)}")
    for row in rows:
        if not 1 <= len(row) <= 3:
            raise ValueError(f"a signature row is (role, surname, date); got {row!r}")

    prims: list = []
    # outer sheet line (thin) and the frame (thick)
    outer = ((0, 0), (width, 0), (width, height), (0, height))
    for a, b in zip(outer, outer[1:] + outer[:1], strict=True):
        prims.append(Seg(a, b, THIN))
    fl, fr = FRAME_MARGINS["left"], width - FRAME_MARGINS["right"]
    fb, ft = FRAME_MARGINS["bottom"], height - FRAME_MARGINS["top"]
    inner = ((fl, fb), (fr, fb), (fr, ft), (fl, ft))
    for a, b in zip(inner, inner[1:] + inner[:1], strict=True):
        prims.append(Seg(a, b, THICK))

    ox, oy = width - FORM3["offset_from_sheet_right"], fb

    def at(x, y):
        return (ox + x, oy + y)

    for x1, y1, x2, y2 in FORM3["thick"]:
        prims.append(Seg(at(x1, y1), at(x2, y2), THICK))
    for x1, y1, x2, y2 in FORM3["thin"]:
        prims.append(Seg(at(x1, y1), at(x2, y2), THIN))

    fitted: list[dict] = []
    for text, cx, cy, max_w in FORM3["headers"]:
        prims.append(_fit_line(text, at(cx, cy), FORM3["header_height"], max_w, "MC", [], "header"))
    sig_h = FORM3["signature_height"]
    for i, row in enumerate(rows):
        y = FORM3["signature_top_y"] - FORM3["signature_step"] * i
        for value, (x, max_w, align) in zip(row, FORM3["signature_columns"], strict=False):
            if not str(value).strip():
                continue
            # left-aligned cells: the text's middle-left on the row centre line
            prims.append(
                _fit_line(
                    str(value).strip(),
                    at(x, y),
                    sig_h,
                    max_w,
                    "ML" if align == "L" else "MC",
                    fitted,
                    f"signature {i + 1}",
                )
            )
    for graph, text in sorted(graphs.items()):
        cx, cy, h, max_w, wrap = FORM3["fields"][graph]
        prims.extend(_fit(text, *at(cx, cy), h, max_w, wrap, fitted, graph))

    label = f"Формат {key.replace('A', 'А')}"
    prims.append(
        Txt(
            (width - FORMAT_LABEL["from_right"], FORMAT_LABEL["y"]),
            label,
            FORMAT_LABEL["height"],
            THICK,
            "MC",
        )
    )
    if extra_columns:
        for x1, y1, x2, y2 in EXTRA_COLUMNS["thick"]:
            prims.append(Seg((x1, y1), (x2, y2), THICK))
        for text, y in EXTRA_COLUMNS["labels"]:
            prims.append(
                Txt(
                    (EXTRA_COLUMNS["label_x"], y),
                    text,
                    EXTRA_COLUMNS["label_height"],
                    THICK,
                    "MC",
                    EXTRA_COLUMNS["label_width_factor"],
                    90.0,
                )
            )
    return {
        "prims": prims,
        "size": (width, height),
        "format": key,
        "orientation": orient,
        "frame": (fl, fb, fr, ft),
        "stamp_origin": (ox, oy),
        "fitted": fitted,
    }
