"""Linetypes.

Split out of the single 1854-line ``AutoCADBackend`` ABC in v1.5.0 (M7).
``AutoCADBackend`` composes every contract in this package, so importing
``backends.base.AutoCADBackend`` is unchanged for callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LinetypeContract(ABC):
    @abstractmethod
    async def linetype_list(self) -> list[str]: ...

    @abstractmethod
    async def linetype_load(
        self,
        name: str,
        file: str | None = None,
    ) -> dict: ...

    @abstractmethod
    async def linetype_define(
        self,
        name: str,
        pattern: list[float],
        description: str = "",
    ) -> dict:
        """Create a linetype from a dash pattern (v1.7), both engines.

        ``pattern`` follows AutoCAD's .lin convention in drawing units: a
        positive number is a dash, a negative one a gap, 0 a dot - e.g. the
        ГОСТ 2.303 dash-dot ``[15, -2, 0, -2]``. A name that already exists is
        left exactly as it is and reported ``already_loaded``. Validated by
        :func:`validate_linetype_pattern` before anything is written.
        """
        ...


def validate_linetype_pattern(pattern) -> list[float]:
    """The pattern as floats, or ValueError naming what is wrong."""
    if not isinstance(pattern, (list, tuple)) or not pattern:
        raise ValueError("pattern must be a non-empty list of numbers (dash > 0, gap < 0, dot 0)")
    out = []
    for value in pattern:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"pattern element {value!r} is not a number")
        out.append(float(value))
    if len(out) > 12:
        raise ValueError("a linetype pattern holds at most 12 elements")
    if not any(v < 0 for v in out):
        raise ValueError("a pattern needs at least one gap (a negative number)")
    return out
