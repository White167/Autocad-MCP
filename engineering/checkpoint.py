"""``checkpoint`` / ``diff_since``: what my last edits did, without re-reading the drawing.

After a series of edits a client wants to know exactly one thing - what
changed - and has two expensive ways to find out: list every entity again, or
look at a picture. A checkpoint is a snapshot of the document
(``engineering/understand/snapshot.py``) held in the server's memory;
``diff_since`` takes a fresh one and matches the two with the revision engine
of ``drawing_diff`` (``engineering/understand/diff.py``: identical signature,
then the same handle, then the nearest entity of its type and layer), and
reports only the added, changed and removed entities, one short line each.

Checkpoints are per server process and per document. A checkpoint taken on
another document (a different ``Snapshot.source``) is refused rather than
diffed: two drawings would read as "everything removed, everything added".
They do not survive a restart, and nothing is written to the drawing.

Pure apart from the in-memory :class:`CheckpointStore`.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from engineering.understand.diff import anchor, diff_snapshots
from engineering.understand.snapshot import EntityRecord, Snapshot

__all__ = [
    "DEFAULT_LIMIT",
    "Checkpoint",
    "CheckpointStore",
    "compact_diff",
    "describe_record",
    "render_diff_text",
]

DEFAULT_LIMIT = 30
#: Checkpoints a process keeps; the oldest name is dropped beyond this.
MAX_CHECKPOINTS = 16


@dataclass
class Checkpoint:
    name: str
    snapshot: Snapshot
    taken_at: float = field(default_factory=time.monotonic)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.taken_at


class CheckpointStore:
    """Named snapshots, newest last; bounded at :data:`MAX_CHECKPOINTS`."""

    def __init__(self) -> None:
        self._items: dict[str, Checkpoint] = {}

    def put(self, name: str, snapshot: Snapshot) -> Checkpoint:
        self._items.pop(name, None)
        cp = Checkpoint(name, snapshot)
        self._items[name] = cp
        while len(self._items) > MAX_CHECKPOINTS:
            self._items.pop(next(iter(self._items)))
        return cp

    def get(self, name: str) -> Checkpoint | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()


def _n(v) -> str:
    return f"{round(float(v), 3):g}"


def _pt(p) -> str:
    return f"{_n(p[0])},{_n(p[1])}"


def describe_record(rec: EntityRecord) -> str:
    """One short phrase for an entity: what a reader needs to recognise it."""
    t = rec.type
    if t == "LINE" and len(rec.points) == 2:
        geo = f"{_pt(rec.points[0])}->{_pt(rec.points[1])}"
    elif t in ("CIRCLE", "ARC") and rec.points and rec.radius is not None:
        geo = f"c {_pt(rec.points[0])} r {_n(rec.radius)}"
        if t == "ARC" and rec.angles:
            geo += f" {_n(rec.angles[0])}-{_n(rec.angles[1])}°"
    elif t in ("LWPOLYLINE", "POLYLINE") and rec.points:
        geo = f"{len(rec.points)} pts from {_pt(rec.points[0])}" + (" closed" if rec.closed else "")
    elif t in ("TEXT", "MTEXT", "ATTRIB"):
        text = (rec.text or "").replace("\n", " ")
        geo = f'"{text[:40]}"' + (f" h {_n(rec.height)}" if rec.height else "")
    elif t == "INSERT":
        geo = f"{rec.block}" + (f" @{_pt(rec.points[0])}" if rec.points else "")
    else:
        at = anchor(rec)
        geo = f"@{_pt(at)}" if at else ""
    return f"{t} {rec.layer} {geo}".rstrip()


def _change_phrase(changes: dict) -> str:
    parts = []
    for key, value in changes.items():
        if key == "moved_by":
            parts.append(f"moved {_n(value[0])},{_n(value[1])}")
        elif key == "geometry":
            parts.append("geometry")
        elif key == "attribs":
            parts.append("attribs " + ",".join(str(a.get("tag", "?")) for a in value))
        elif isinstance(value, dict) and "from" in value:
            a, b = value["from"], value["to"]
            if isinstance(a, float) or isinstance(b, float):
                a = _n(a) if a is not None else a
                b = _n(b) if b is not None else b
            if isinstance(a, list) or isinstance(b, list):
                parts.append(key)
            else:
                parts.append(f"{key} {a}->{b}")
        else:
            parts.append(key)
    return "; ".join(parts)


def compact_diff(
    old: Snapshot, new: Snapshot, *, tol: float = 0.01, limit: int = DEFAULT_LIMIT
) -> dict:
    """The diff of ``old`` -> ``new`` as short rows, each list capped at ``limit``."""
    if int(limit) < 1:
        raise ValueError(f"limit must be at least 1; got {limit!r}")
    # One document at two moments: an edit keeps its handle, so the handle pass
    # pairs every edited entity and the position pass only pairs entities a
    # delete-and-redraw replaced.
    result = diff_snapshots(old, new, tol=tol, trust_handles=True)
    by_handle_new = {rec.handle: rec for rec in new.records}
    by_handle_old = {rec.handle: rec for rec in old.records}

    def rows(items, records, *, changed=False):
        out = []
        for row in items[: int(limit)]:
            if changed:
                rec = records.get(row["new_handle"])
                handle = row["new_handle"]
                text = f"{row['type']} {row['layer']}: {_change_phrase(row['changes'])}"
                if row["old_handle"] != row["new_handle"]:
                    text += f" (was {row['old_handle']})"
            else:
                rec = records.get(row["handle"])
                handle = row["handle"]
                text = describe_record(rec) if rec is not None else f"{row['type']} {row['layer']}"
            out.append({"handle": handle, "what": text})
        return out

    summary = result["summary"]
    return {
        "summary": {
            "before": summary["old_entities"],
            "after": summary["new_entities"],
            "added": summary["added"],
            "changed": summary["changed"],
            "removed": summary["removed"],
        },
        "added": rows(result["added"], by_handle_new),
        "changed": rows(result["changed"], by_handle_new, changed=True),
        "removed": rows(result["removed"], by_handle_old),
        "truncated": {
            key: max(0, len(result[key]) - int(limit)) for key in ("added", "changed", "removed")
        },
    }


def render_diff_text(d: dict, *, name: str, age_s: float | None) -> str:
    s = d["summary"]
    age = "" if age_s is None or not math.isfinite(age_s) else f", {age_s:.0f}s ago"
    lines = [
        f'SINCE "{name}"{age}: +{s["added"]} ~{s["changed"]} -{s["removed"]} '
        f"({s['before']} -> {s['after']} entities)"
    ]
    for key, mark in (("added", "+"), ("changed", "~"), ("removed", "-")):
        for row in d[key]:
            lines.append(f"{mark} {row['handle']} {row['what']}")
        more = d["truncated"][key]
        if more:
            lines.append(f"{mark} ... {more} more")
    if not (s["added"] or s["changed"] or s["removed"]):
        lines.append("no changes")
    return "\n".join(lines)
