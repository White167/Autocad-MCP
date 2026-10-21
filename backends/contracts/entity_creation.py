"""Creating geometry.

Split out of the single 1854-line ``AutoCADBackend`` ABC in v1.5.0 (M7).
``AutoCADBackend`` composes every contract in this package, so importing
``backends.base.AutoCADBackend`` is unchanged for callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from backends.capability import capability

if TYPE_CHECKING:
    from backends.base import EntityInfo


#: TEXT justification codes -> (DXF 72 horizontal, DXF 73 vertical), as AutoCAD's
#: TEXT command names them: L / C / R / M on the baseline (M = middle of the
#: text), then T / M / B rows x L / C / R columns.
TEXT_ALIGN_CODES: dict[str, tuple[int, int]] = {
    "L": (0, 0),
    "C": (1, 0),
    "R": (2, 0),
    "M": (4, 0),
    "BL": (0, 1),
    "BC": (1, 1),
    "BR": (2, 1),
    "ML": (0, 2),
    "MC": (1, 2),
    "MR": (2, 2),
    "TL": (0, 3),
    "TC": (1, 3),
    "TR": (2, 3),
}


def text_align_code(align: str | None) -> str | None:
    """Normalise a justification code; refuse an unknown one with the list."""
    if align is None:
        return None
    code = str(align).strip().upper()
    if code not in TEXT_ALIGN_CODES:
        raise ValueError(
            f"align {align!r} is not a TEXT justification; use one of {', '.join(TEXT_ALIGN_CODES)}"
        )
    return code


class EntityCreationContract(ABC):
    @abstractmethod
    async def entity_create_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        z1: float = 0.0,
        z2: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
        linetype: str | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_circle(
        self,
        cx: float,
        cy: float,
        radius: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_arc(
        self,
        cx: float,
        cy: float,
        radius: float,
        start_angle: float,
        end_angle: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_polyline(
        self,
        points: list[list[float]],
        closed: bool = False,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_text(
        self,
        text: str,
        x: float,
        y: float,
        height: float = 2.5,
        rotation: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
        style: str | None = None,
        align: str | None = None,
        width_factor: float | None = None,
    ) -> EntityInfo:
        """``style`` must name an existing text style (refused by name otherwise);
        ``align`` is one of :data:`TEXT_ALIGN_CODES` and makes ``(x, y)`` the
        alignment point rather than the left baseline start (v1.7)."""
        ...

    @abstractmethod
    async def entity_create_mtext(
        self,
        text: str,
        x: float,
        y: float,
        width: float = 100.0,
        height: float = 2.5,
        rotation: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_table(
        self,
        x: float,
        y: float,
        rows: list[list[str]],
        headers: list[str] | None = None,
        column_widths: list[float] | None = None,
        row_height: float = 7.0,
        text_height: float = 2.5,
        title: str | None = None,
        layer: str = "TEXT",
    ) -> EntityInfo: ...

    @abstractmethod
    async def leader_create_mleader(
        self,
        points: list[list[float]],
        text: str,
        text_height: float = 2.5,
        landing_gap: float = 1.0,
        arrow_size: float = 2.5,
        layer: str = "DIM",
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_hatch(
        self,
        pattern: str,
        boundary_points: list[list[float]],
        scale: float = 1.0,
        angle: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_spline(
        self,
        fit_points: list[list[float]],
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_ellipse(
        self,
        cx: float,
        cy: float,
        major_x: float,
        major_y: float,
        ratio: float = 0.5,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_point(
        self,
        x: float,
        y: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_block_ref(
        self,
        name: str,
        x: float,
        y: float,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> EntityInfo: ...

    # ── hatch depth (M8 / F4) ────────────────────────────────────────────────

    @abstractmethod
    async def hatch_set_gradient(
        self,
        handle: str,
        color1: list[int],
        color2: list[int],
        rotation: float = 0.0,
        centered: float = 0.0,
        one_color: bool = False,
        tint: float = 0.0,
        name: str = "LINEAR",
    ) -> dict:
        """Replace a hatch's fill with a two-colour gradient. ``{ok, handle, gradient}``."""
        ...

    @abstractmethod
    async def hatch_edit(
        self,
        handle: str,
        pattern: str = "",
        scale: float | None = None,
        angle: float | None = None,
        color: int | None = None,
        style: str = "",
    ) -> dict:
        """Edit an existing hatch in place. Returns ``{ok, handle, changed}``.

        ``changed`` names exactly the attributes that moved: an omitted
        parameter must leave its attribute alone, because a partial edit that
        quietly resets the rest is data loss. ``style`` is the island style —
        ``normal``, ``outer`` or ``ignore``.
        """
        ...

    @abstractmethod
    async def hatch_add_boundary(self, handle: str, edges: list[dict]) -> dict:
        """Add one boundary path built from typed edges.

        Returns ``{ok, handle, path_count, edge_types}``. Edges are
        ``{"type": "line"|"arc"|"ellipse", ...}``. Typed edges exist because a
        boundary API that only accepts vertex lists silently straightens every
        curve it is given.
        """
        ...

    # ── annotation objects (M8 / F15) ────────────────────────────────────────

    @capability(  # noqa: B027 — @capability supplies the body
        "wipeout",
        reason=(
            "ActiveX has no wipeout constructor — ModelSpace.AddWipeout does not exist "
            "(verified against AutoCAD 2026); WIPEOUT is a command only. Use the headless "
            "backend (AUTOCAD_MCP_BACKEND=ezdxf), or run WIPEOUT in AutoCAD directly."
        ),
    )
    async def entity_create_wipeout(
        self, points: list[list[float]], layer: str | None = None
    ) -> dict:
        """Mask the drawing behind a closed polygon. Returns ``{ok, handle, points}``.

        Refuses fewer than three points: a zero-area mask masks nothing while
        reporting success.
        """
        ...

    @capability(  # noqa: B027 — @capability supplies the body
        "revcloud",
        reason=(
            "ActiveX exposes no revision-cloud member and driving the REVCLOUD command "
            "blind against a live drawing is unverified. Use the headless backend "
            "(AUTOCAD_MCP_BACKEND=ezdxf), or run REVCLOUD in AutoCAD directly."
        ),
    )
    async def entity_create_revcloud(
        self,
        points: list[list[float]],
        segment_length: float,
        layer: str | None = None,
        closed: bool = True,
    ) -> dict:
        """Draw a revision cloud along a path. Returns ``{ok, handle, segments}``.

        A revision cloud *is* a polyline whose every segment carries an arc
        bulge; a segment length longer than the path yields no arcs at all, so
        that case is refused rather than returned as a rectangle.
        """
        ...
