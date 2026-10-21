"""``ACADMCP_SPDS`` XDATA: what an СПДС tool drew, written onto the drawing.

The roadmap's rule for the pack (stage 4): a result is written with XDATA so it
can be read and updated rather than redrawn. Three payload kinds, through the
shared chunking codec of ``engineering/mech/xdata.py``:

    {"v": 1, "kind": "sheet", "id": "<hex>", "form": 3, "format": "A3",
     "orientation": "landscape", "layout": "Лист1", "origin": [0, 0],
     "scale": 1, "frame": [...], "stamp_origin": [...],
     "graphs": {"1": "КП-2026-КЖ", ...}}                 on the sheet's first line
    {"v": 1, "kind": "field", "sheet": "<hex>", "graph": 4, "line": 0,
     "h": 3.5}                                            on each stamp text
    {"v": 1, "kind": "mark", "mark": "axis" | "elevation" | "slope" |
     "section" | "leader" | "soil", "scale": 100, "h": 5.0, ...}   on a mark's text

``h`` is the text height in mm of paper; the drawn height is ``h x scale``.
An axis mark adds ``label``, ``dir`` (``x``: a vertical axis, numbered left to
right; ``y``: a horizontal one, bottom up) and ``at`` (its position); a level
adds ``value``, ``decimals``, ``signed`` and ``kind``.

Readers go through :func:`read_index`, which takes an ``ezdxf`` document (the
live engine's ``Document.Export`` snapshot, or the headless document) and reads
every space in one pass. A payload that is not ours is skipped, never guessed
at - the codec refuses it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping

from engineering.mech import xdata as _codec

APP_ID = "ACADMCP_SPDS"
KINDS: tuple[str, ...] = ("sheet", "field", "mark")

__all__ = ["APP_ID", "KINDS", "decode", "new_id", "read_index", "to_values", "write"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def to_values(payload: dict) -> list[str]:
    return _codec.to_values(payload, app_id=APP_ID, kinds=KINDS)


def decode(rows) -> dict:
    return _codec.decode(rows, app_id=APP_ID, kinds=KINDS)


async def write(backend, handle: str, payload: dict) -> None:
    """Attach ``payload`` to ``handle`` (refused by the codec before writing)."""
    await backend.entity_set_xdata(handle, APP_ID, to_values(payload))


RecordFn = Callable[[object], "dict | None"]


def _layout_of(entity, owners: Mapping[str, str]) -> str:
    return owners.get(str(entity.dxf.owner), "Model")


def read_index(doc) -> dict:
    """Every ``ACADMCP_SPDS`` record of ``doc``, with what the entity now holds.

    ``{"sheets": [...], "fields": [...], "marks": [...], "texts": [...]}``:
    each record row is its payload plus ``handle``, ``layout``, ``layer`` and,
    for a text, ``text`` / ``height`` / ``insert``. ``texts`` lists every TEXT
    and MTEXT of the drawing (handle, layout, layer, height, tagged) for the
    focuses that judge untagged text too. Read-only.
    """
    owners = {str(layout.block_record_handle): layout.name for layout in doc.layouts}
    out: dict = {"sheets": [], "fields": [], "marks": [], "texts": []}
    for layout in doc.layouts:
        for entity in layout:
            kind = entity.dxftype()
            row = None
            if entity.has_xdata(APP_ID):
                try:
                    payload = decode(list(entity.get_xdata(APP_ID)))
                except (ValueError, TypeError):
                    payload = None
                if payload is not None:
                    row = {
                        **payload,
                        "handle": entity.dxf.handle,
                        "layout": _layout_of(entity, owners),
                        "layer": entity.dxf.layer,
                    }
                    if kind in ("TEXT", "MTEXT"):
                        row["text"] = entity.plain_text() if kind == "MTEXT" else entity.dxf.text
                        height = entity.dxf.char_height if kind == "MTEXT" else entity.dxf.height
                        row["height"] = float(height)
                        row["insert"] = [float(entity.dxf.insert[0]), float(entity.dxf.insert[1])]
                    out[payload["kind"] + "s"].append(row)
            if kind in ("TEXT", "MTEXT"):
                height = entity.dxf.char_height if kind == "MTEXT" else entity.dxf.height
                out["texts"].append(
                    {
                        "handle": entity.dxf.handle,
                        "layout": _layout_of(entity, owners),
                        "layer": entity.dxf.layer,
                        "height": float(height),
                        "tagged": row is not None,
                    }
                )
    return out
