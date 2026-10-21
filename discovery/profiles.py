"""TOOL_CONFIG: one file that says which tools a client is shown, and how.

``TOOL_PROFILE`` and ``TOOL_PACKS`` choose a surface from two fixed menus. A
client that drafts construction drawings to СПДС wants neither the gear tools
nor the ISO title block, and neither menu can say so. A tool config can::

    # discovery/profiles/spds.toml
    base           = "full"                 # or "lean": TOOL_PROFILE's value
    packs          = ["core", "settings"]   # or "all": TOOL_PACKS' value
    exclude_groups = ["gear", "gdt"]        # by tag (tags={...} on @mcp.tool)
    exclude        = ["sheet_frame"]        # by name
    include        = ["document_list"]      # by name, wins over every exclusion above
    costs          = ["read", "safe"]       # keep only these @cad_tool costs
    descriptions   = "short"                # see discovery/compact.py
    discovery      = "hybrid"               # "off" | "search" | "hybrid"
    pinned         = ["entity_create_line"] # hybrid: listed whole; the rest via search_tools

Every key is optional. A key the file sets wins over the environment variable
it replaces (``base`` over ``TOOL_PROFILE``, ``packs`` over ``TOOL_PACKS``); a
key it leaves out falls back to that variable. ``include`` cannot lift the
``ENABLE_3D`` gate: a disabled solid would only refuse the call.

``TOOL_CONFIG`` is either a path to a ``.toml`` file or the name of a bundled
preset (:func:`preset_names`). A config that cannot be read never blocks
startup: the server warns, serves the environment's surface and reports the
error in ``system_about.tool_profile.tool_config``.

Kept free of ``fastmcp`` imports like the rest of :mod:`discovery`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DESCRIPTION_MODES",
    "DISCOVERY_MODES",
    "PRESETS_DIR",
    "ToolConfig",
    "ToolConfigError",
    "load_tool_config",
    "preset_names",
]

PRESETS_DIR = Path(__file__).with_name("profiles")

#: ``full`` - the docstrings and schemas as written; ``short`` - the compact
#: listing of :mod:`discovery.compact`.
DESCRIPTION_MODES = ("full", "short")

#: DISCOVERY_MODE's values; ``hybrid`` lists ``pinned`` whole and the rest of
#: the enabled surface through search_tools / call_tool.
DISCOVERY_MODES = ("off", "search", "hybrid")

_LIST_KEYS = ("exclude_groups", "exclude", "include", "costs", "pinned")
_KNOWN_KEYS = frozenset(("base", "packs", "descriptions", "description", "discovery", *_LIST_KEYS))


class ToolConfigError(ValueError):
    """A TOOL_CONFIG value that names no file and no preset, or a file that does not parse."""


@dataclass(frozen=True)
class ToolConfig:
    """A parsed tool config. ``None`` means "the file does not set this key"."""

    source: str | None = None
    path: str | None = None
    description: str | None = None
    base: str | None = None
    packs: tuple[str, ...] | None = None
    exclude_groups: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    costs: tuple[str, ...] | None = None
    descriptions: str | None = None
    discovery: str | None = None
    pinned: tuple[str, ...] = ()
    ignored_keys: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return self.source is None

    def report(self) -> dict:
        """The block ``system_about`` shows; empty keys are left out."""
        out: dict = {"source": self.source}
        for key in ("path", "description", "base", "descriptions", "discovery"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.packs is not None:
            out["packs"] = list(self.packs)
        if self.costs is not None:
            out["costs"] = list(self.costs)
        for key in ("exclude_groups", "exclude", "include", "pinned", "ignored_keys"):
            value = getattr(self, key)
            if value:
                out[key] = list(value)
        return out


def preset_names() -> list[str]:
    """The bundled presets, by file stem."""
    if not PRESETS_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESETS_DIR.glob("*.toml"))


def _str_list(raw: object, key: str, where: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ToolConfigError(f"{where}: {key!r} must be a list of strings")
    # Tool names are matched as written; tags, packs and costs are lower-case.
    fold = key not in ("include", "exclude", "pinned")
    return tuple((item.strip().lower() if fold else item.strip()) for item in raw if item.strip())


def _parse(data: dict, *, source: str, path: Path) -> ToolConfig:
    where = str(path)
    ignored = tuple(sorted(k for k in data if k not in _KNOWN_KEYS))
    base = data.get("base")
    if base is not None and not isinstance(base, str):
        raise ToolConfigError(f"{where}: 'base' must be a string")
    packs_raw = data.get("packs")
    packs: tuple[str, ...] | None = None
    if packs_raw is not None:
        packs = _str_list(packs_raw, "packs", where)
    descriptions = data.get("descriptions")
    if descriptions is not None:
        if not isinstance(descriptions, str) or descriptions.lower() not in DESCRIPTION_MODES:
            raise ToolConfigError(
                f"{where}: 'descriptions' must be one of {', '.join(DESCRIPTION_MODES)}"
            )
        descriptions = descriptions.lower()
    discovery = data.get("discovery")
    if discovery is not None:
        if not isinstance(discovery, str) or discovery.lower() not in DISCOVERY_MODES:
            raise ToolConfigError(
                f"{where}: 'discovery' must be one of {', '.join(DISCOVERY_MODES)}"
            )
        discovery = discovery.lower()
    costs = data.get("costs")
    note = data.get("description")
    return ToolConfig(
        source=source,
        path=where,
        description=note if isinstance(note, str) else None,
        base=base.lower().strip() if isinstance(base, str) else None,
        packs=packs,
        exclude_groups=_str_list(data.get("exclude_groups", []), "exclude_groups", where),
        exclude=_str_list(data.get("exclude", []), "exclude", where),
        include=_str_list(data.get("include", []), "include", where),
        costs=_str_list(costs, "costs", where) if costs is not None else None,
        descriptions=descriptions,
        discovery=discovery,
        pinned=_str_list(data.get("pinned", []), "pinned", where),
        ignored_keys=ignored,
    )


def load_tool_config(value: str | None) -> ToolConfig:
    """Resolve ``TOOL_CONFIG``: empty → no config; a file path; or a preset name.

    Raises :class:`ToolConfigError` naming the presets when the value is neither.
    """
    value = (value or "").strip()
    if not value:
        return ToolConfig()
    candidate = Path(value).expanduser()
    if candidate.suffix.lower() == ".toml" or candidate.is_file():
        if not candidate.is_file():
            raise ToolConfigError(f"TOOL_CONFIG file not found: {candidate}")
        path, source = candidate.resolve(), "file"
    else:
        path, source = PRESETS_DIR / f"{value.lower()}.toml", f"preset:{value.lower()}"
        if not path.is_file():
            raise ToolConfigError(
                f"TOOL_CONFIG {value!r} is neither a .toml file nor a preset. "
                f"Presets: {', '.join(preset_names()) or 'none'}."
            )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolConfigError(f"TOOL_CONFIG {path}: {exc}") from exc
    return _parse(data, source=source, path=path)
