"""One call to put a drawing on a drafting standard: styles, units, layers.

Composes only backend methods that already exist (``textstyle_*``,
``dimstyle_*``, ``system_get/set_variable``, ``drawing_apply_iso_layers``),
so it runs on both engines and needs nothing from the settings facade. Units
are the five system variables both standards share; the decimal separator is
the preset's and arrives through the dimension style. ANSI layer naming is
company-specific, so both standards use the ``mech`` layer set — only styles
and the separator differ (spec §4.2).

``gost`` is the СПДС route: it runs ``spds_setup`` (ГОСТ linetypes, the СПДС
layer set, the GOST text style, ``СПДС_1-M`` for the drawing scale ``M``, the
units) and makes that dimension style and the GOST text style current - one
definition of what an СПДС drawing needs, not a second copy here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engineering.standards.dimstyles import resolve_dimstyle
from engineering.standards.textstyles import TEXT_PRESETS

if TYPE_CHECKING:
    from backends.base import AutoCADBackend

__all__ = [
    "GOST",
    "STANDARDS",
    "STANDARD_SYSVARS",
    "apply_standard",
    "resolve_standard",
]

STANDARDS: dict[str, dict[str, str]] = {
    "iso": {"preset": "iso-25", "dimstyle": "ISO-25", "textstyle": "ISOCP", "layer_set": "mech"},
    "ansi": {"preset": "ansi", "dimstyle": "ANSI", "textstyle": "ROMANS", "layer_set": "mech"},
}

#: Millimetres, decimal linear and angular units, 1:1 linetype and dimension
#: scale. Written by name so the report can say what moved.
STANDARD_SYSVARS: tuple[tuple[str, Any], ...] = (
    ("INSUNITS", 4),
    ("LUNITS", 2),
    ("AUNITS", 0),
    ("LTSCALE", 1.0),
    ("DIMSCALE", 1.0),
)


#: The СПДС standard, applied through ``engineering/spds/setup.py``.
GOST = "gost"


def resolve_standard(standard: str) -> tuple[str, dict[str, str]]:
    """``(key, row)`` for ``iso`` / ``ansi`` (any case), else ``ValueError``."""
    key = str(standard).strip().lower() if standard is not None else ""
    if key not in STANDARDS:
        raise ValueError(
            f"standard: unknown value {standard!r}; use one of {sorted([*STANDARDS, GOST])}"
        )
    return key, STANDARDS[key]


async def _apply_gost(backend: AutoCADBackend, scale: int | None) -> dict:
    from engineering.spds.setup import spds_setup
    from engineering.spds.standards import TEXT_STYLE, dimstyle_name

    m = 100 if scale is None else int(scale)
    setup = await spds_setup(backend, [m], current_scale=m)
    await backend.textstyle_set_current(TEXT_STYLE)
    name = dimstyle_name(m)
    return {
        "standard": GOST,
        "scale": m,
        "dimstyle": {
            "name": name,
            "created": setup["dimstyles"][name] == "created",
            "current": True,
        },
        "textstyle": {
            "name": TEXT_STYLE,
            "created": setup["text_style"] != "present",
            "current": True,
        },
        "settings": {"variables": setup["variables"], "applied": True},
        "layers": {"layer_set": "spds", "created": setup["layers_created"]},
        "linetypes_created": setup["linetypes_created"],
    }


def _same(old: Any, new: Any) -> bool:
    if old is None:
        return False
    try:
        return abs(float(old) - float(new)) < 1e-9
    except (TypeError, ValueError):
        return str(old) == str(new)


async def apply_standard(
    backend: AutoCADBackend,
    standard: str,
    layers: bool = True,
    units: bool = True,
    scale: int | None = None,
) -> dict:
    """Styles created (or reused) and made current, units written, layers bootstrapped.

    Every item reports whether it was created or already present; ``settings``
    reports only the variables that moved. An existing style of the standard's
    name is used as it is — ``dimstyle_modify`` changes one on purpose.

    Every system variable the call will write is read *before* the first
    style is created, so a read the engine refuses is a refusal with nothing
    written. (The units loop used to run after both styles; on the live
    engine its INSUNITS read went through a member the ActiveX Application
    does not have, so the call failed with ISO-25 and ISOCP already created
    and current.) That first read is only the gate: making a dimension style
    current restores every DIM* variable from the style (ezdxf syncs the
    header; AutoCAD 2026 measured ``ActiveDimStyle = ISO-25`` with a style
    DIMSCALE of 2.0 moving the document's DIMSCALE 1.0 -> 2.0), so each
    variable is read again *after* the style switch and the write is decided
    on that value. Deciding on the gate read skipped the DIMSCALE write
    whenever a pre-existing ISO-25/ANSI carried another scale, and reported
    ``changed: {}`` on a drawing left at the style's scale.

    ``gost`` always writes its layers and units (``spds_setup`` is one unit);
    ``scale`` is its drawing scale denominator (default 100) and is refused
    for ``iso`` / ``ansi``, whose styles are 1:1.
    """
    if str(standard).strip().lower() == GOST:
        if scale is not None and int(scale) <= 0:
            raise ValueError("scale is a positive denominator, e.g. 100 for 1:100")
        return await _apply_gost(backend, scale)
    if scale is not None:
        raise ValueError("scale applies to the gost standard only")
    key, row = resolve_standard(standard)
    if not isinstance(layers, bool) or not isinstance(units, bool):
        raise TypeError("layers and units must be booleans")

    if units:  # the refusal gate: every variable readable before anything is written
        for var, _value in STANDARD_SYSVARS:
            try:
                await backend.system_get_variable(var)
            except Exception as exc:
                raise RuntimeError(
                    f"apply_standard: could not read {var} before writing "
                    f"({exc}); nothing was changed"
                ) from exc

    text_names = {entry["name"].lower() for entry in await backend.textstyle_list()}
    textstyle_created = row["textstyle"].lower() not in text_names
    if textstyle_created:
        font_file, width, oblique = TEXT_PRESETS[row["textstyle"]]
        await backend.textstyle_create(row["textstyle"], font_file, 0.0, width, oblique, False)
    await backend.textstyle_set_current(row["textstyle"])

    dim_names = {entry["name"].lower() for entry in await backend.dimstyle_list()}
    dimstyle_created = row["dimstyle"].lower() not in dim_names
    if dimstyle_created:
        await backend.dimstyle_create(
            row["dimstyle"], resolve_dimstyle(row["preset"], None), set_current=True
        )
    else:
        await backend.dimstyle_set_current(row["dimstyle"])

    changed: dict[str, list] = {}
    if units:
        for var, value in STANDARD_SYSVARS:
            # Re-read after the style switch: the gate value is stale for
            # DIM* once another dimension style has been made current.
            old = await backend.system_get_variable(var)
            if _same(old, value):
                continue
            await backend.system_set_variable(var, value)
            changed[var] = [old, value]

    layer_result = None
    if layers:
        applied = await backend.drawing_apply_iso_layers(row["layer_set"])
        layer_result = {"layer_set": row["layer_set"], "layers": applied["layers"]}

    return {
        "standard": key,
        "dimstyle": {"name": row["dimstyle"], "created": dimstyle_created, "current": True},
        "textstyle": {"name": row["textstyle"], "created": textstyle_created, "current": True},
        "settings": {"changed": changed, "applied": units},
        "layers": layer_result,
    }
