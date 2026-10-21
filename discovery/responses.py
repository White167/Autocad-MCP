"""``responses = "compact"``: what a write tool answers when only the handle matters.

Every ``entity_create_*`` / modify tool answers with the full ``EntityInfo`` -
handle, type, layer, colour, linetype, visibility, every property and a
bounding box, about 200 characters for a circle. A client drawing thirty
objects reads thirty of those and uses thirty handles. In compact mode a write
tool's result that *is* an entity record shrinks to ``{"handle", "type"}``
(a list of them to a list of those), and every float in any result is
rounded to :data:`DIGITS` decimals - ``100.00000000001`` is ``100``.

Read tools (``@cad_tool`` cost ``read``) keep their entity records: reading
the properties is why they were called. ``entity_get`` gives the full record
of anything a compact answer left out.

Kept free of ``fastmcp`` imports, like the rest of :mod:`discovery`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DIGITS", "RESPONSE_MODES", "compact_value", "is_entity_record"]

RESPONSE_MODES = ("full", "compact")

#: Decimals kept on a float in compact mode: 0.1 µm at millimetre units.
DIGITS = 4

_ENTITY_KEYS = frozenset({"handle", "type", "layer"})


def is_entity_record(value: Any) -> bool:
    """An ``EntityInfo`` as a dict: a handle, a type, a layer and its properties."""
    return isinstance(value, dict) and _ENTITY_KEYS <= value.keys() and "properties" in value


def _round(value: Any) -> Any:
    if isinstance(value, float):
        r = round(value, DIGITS)
        return 0.0 if r == 0 else r
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def _shrink(value: Any) -> Any:
    if is_entity_record(value):
        return {"handle": value["handle"], "type": value["type"]}
    if isinstance(value, list) and value and all(is_entity_record(v) for v in value):
        return [{"handle": v["handle"], "type": v["type"]} for v in value]
    if isinstance(value, dict):
        return {
            k: _shrink(v) if k in ("result", "entity", "entities") else v for k, v in value.items()
        }
    return value


def compact_value(value: Any, *, write: bool) -> Any:
    """``value`` rounded, and - for a write tool - with entity records shrunk."""
    return _round(_shrink(value) if write else value)
