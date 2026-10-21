"""``spds_setup``: everything an СПДС drawing needs before its first entity.

The linetypes ``GOST_DASH`` / ``GOST_AXIS`` / ``GOST_2DOT``, the layer set,
the text style ``GOST`` on ``isocpeur.ttf``, one dimension style
``СПДС_1-M`` per drawing scale, and the units the skill sets (millimetres,
decimal, lineweights shown, linetype scales 1). Idempotent: what exists is
left as it is and reported ``present``; nothing is renamed or overwritten.
"""

from __future__ import annotations

from collections.abc import Iterable

from engineering.spds.draw import ensure_resources
from engineering.spds.standards import LAYERS, LINETYPES, dimstyle_name, dimstyle_values

__all__ = ["SETUP_VARIABLES", "spds_setup"]

#: The skill's ``spds:setup`` system variables.
SETUP_VARIABLES = {
    "INSUNITS": 4,  # millimetres
    "LUNITS": 2,  # decimal
    "LWDISPLAY": 1,
    "LTSCALE": 1.0,
    "PSLTSCALE": 1,
    "MSLTSCALE": 1,
}


async def spds_setup(
    backend,
    scales: Iterable[int] = (100,),
    *,
    current_scale: int | None = None,
    current_layer: str | None = "Контур",
) -> dict:
    scales = sorted({int(s) for s in scales})
    if not scales or any(s <= 0 for s in scales):
        raise ValueError("scales are positive denominators, e.g. [100, 50] for 1:100 and 1:50")
    if current_scale is not None and int(current_scale) not in scales:
        raise ValueError(f"current_scale {current_scale} is not among scales {scales}")
    made = await ensure_resources(backend, list(LAYERS), text_style=True)
    # every ГОСТ linetype, also the ones no layer of the set uses (GOST_2DOT)
    have = {str(n).lower() for n in await backend.linetype_list()}
    for name, (pattern, description) in LINETYPES.items():
        if name.lower() not in have:
            await backend.linetype_define(name, list(pattern), description)
            made["linetypes"].append(name)

    variables: dict[str, object] = {}
    for name, value in SETUP_VARIABLES.items():
        try:
            await backend.system_set_variable(name, value)
            variables[name] = value
        except Exception as exc:  # a CAD without the variable: say so, keep going
            variables[name] = f"skipped: {str(exc).split('.')[0][:80]}"

    existing = {row["name"].lower() for row in await backend.dimstyle_list()}
    dimstyles = {}
    for scale in scales:
        name = dimstyle_name(scale)
        if name.lower() in existing:
            dimstyles[name] = "present"
            continue
        await backend.dimstyle_create(name, dimstyle_values(scale))
        dimstyles[name] = "created"
    if current_scale is not None:
        await backend.dimstyle_set_current(dimstyle_name(current_scale))
    if current_layer:
        await backend.layer_set_current(current_layer)
    return {
        "ok": True,
        "linetypes_created": made["linetypes"],
        "layers_created": made["layers"],
        "layers": list(LAYERS),
        "text_style": made["text_style"] or "present",
        "dimstyles": dimstyles,
        "current_dimstyle": dimstyle_name(current_scale) if current_scale else None,
        "current_layer": current_layer,
        "variables": variables,
    }
