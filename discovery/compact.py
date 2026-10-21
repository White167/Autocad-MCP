"""``descriptions = "short"``: the tool listing a model reads, without the essay.

A client forwards each tool's name, description and input schema to the model
on every turn. Measured on v1.6.0's full surface that is ~250 000 characters,
and the schemas outweigh the descriptions (136k vs 96k): 939 parameters, each
with a sentence or a paragraph, and every optional one spelled
``anyOf: [{type: X}, {type: null}], default: null``.

The short listing keeps everything a model needs to *call* a tool correctly and
drops what it only needs to *choose* one carefully:

* the description becomes the tool's ``@cad_tool`` summary - one line written
  for exactly this purpose - and the full docstring stays one ``tool_help``
  call away;
* a parameter description is cut to its first sentence, at most
  :data:`PARAM_DESCRIPTION_CHARS` characters; the parameters whose meaning is
  the same on every tool (:data:`CONVENTIONAL_PARAMS` - coordinates, ``handle``,
  ``layer``, ``color``, ``linetype``) lose theirs, because the server
  instructions state the convention once;
* ``anyOf`` with ``null`` collapses to the non-null branch and a ``null``
  default is dropped - omitting an optional argument is how a model says
  "none", and the server still accepts an explicit ``null``;
* ``additionalProperties: false`` and ``title`` go: neither changes a call.

Types, enums, bounds, defaults and ``required`` are never touched, so a call
the short schema allows is a call the real schema allows. Only the *listing* is
rewritten - :class:`CompactListingTransform` does not implement ``get_tool``,
so a call still runs against the registered tool, its full schema and its
pydantic validation.

Kept free of ``fastmcp`` imports at module scope, like the rest of
:mod:`discovery`; the transform class is built on first use.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "CONVENTIONAL_PARAMS",
    "PARAM_DESCRIPTION_CHARS",
    "SHORT_INSTRUCTIONS",
    "compact_schema",
    "first_sentence",
    "make_compact_transform",
    "short_description",
]

#: Hard cap on a parameter description in the short listing.
PARAM_DESCRIPTION_CHARS = 90

#: Parameters whose meaning is the server-wide convention stated once in its
#: instructions: WCS drawing units, a hex handle, a layer name (current layer
#: when omitted), an ACI colour (256 ByLayer, 0 ByBlock), a linetype name. A
#: tool whose parameter of this name means something else must not rely on its
#: description surviving - none does as of v1.6.0 (checked by the test suite).
CONVENTIONAL_PARAMS = frozenset(
    {
        "x",
        "y",
        "z",
        "x1",
        "y1",
        "z1",
        "x2",
        "y2",
        "z2",
        "cx",
        "cy",
        "handle",
        "layer",
        "color",
        "linetype",
    }
)

#: The server instructions under the short listing. They state once what the
#: dropped parameter descriptions said on every tool (CONVENTIONAL_PARAMS), and
#: nothing discipline-specific: a СПДС client should not read about gear teeth.
SHORT_INSTRUCTIONS = """\
AutoCAD MCP Pro: live AutoCAD (COM) or headless DXF (ezdxf) drafting.

Tool descriptions are one-line summaries. Before using a tool whose behaviour
the summary does not settle, call tool_help(name): it returns the full text,
where refusals, units and engine differences are written.

Conventions for every tool:
- x, y, z, x1..y2, cx, cy: WCS coordinates in drawing units (mm by default).
  Angles in degrees, counter-clockwise from +X.
- handle: the entity's hex handle, returned by every create/copy tool.
- layer: layer name; omitted means the current layer.
- color: ACI 1-255; 256 = ByLayer, 0 = ByBlock. linetype: a loaded linetype name.
- Omit an optional argument to leave it unset; do not pass null.

Working economically: batch a series of edits with cad_batch; inspect with
entity_list(fields=[...]) or analysis_* rather than a screenshot; take a
screenshot only at the end of a stage, zoomed to the area that matters.
"""

_SENTENCE_END = re.compile(r"(?<=[.;!?])\s")


def first_sentence(text: str | None, limit: int = PARAM_DESCRIPTION_CHARS) -> str:
    """The first sentence of ``text`` on one line, cut to ``limit`` characters.

    A full stop inside ``e.g.`` or a number does not end the sentence: only a
    stop followed by whitespace does.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    head = _SENTENCE_END.split(flat, maxsplit=1)[0]
    if head.lower().endswith(("e.g.", "i.e.", "etc.", "vs.")) and len(head) < len(flat):
        # The split landed after an abbreviation; take the next sentence too.
        rest = _SENTENCE_END.split(flat[len(head) :].lstrip(), maxsplit=1)[0]
        head = f"{head} {rest}"
    if len(head) > limit:
        head = head[: limit - 1].rstrip(" ,;:") + "…"
    return head


def _is_null(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("type") == "null" and len(schema) == 1


def compact_schema(schema: Any, *, _param: str | None = None) -> Any:
    """Return a compact copy of a JSON schema (see the module docstring)."""
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    any_of = schema.get("anyOf")
    null_union = isinstance(any_of, list) and any(_is_null(b) for b in any_of)
    if null_union:
        rest = [b for b in any_of if not _is_null(b)]
        if len(rest) == 1 and isinstance(rest[0], dict):
            out.update(compact_schema(rest[0]))
        elif rest:
            out["anyOf"] = compact_schema(rest)
    for key, value in schema.items():
        if key == "anyOf" and null_union:
            continue
        if key == "additionalProperties" and value is False:
            continue
        if key == "title" and isinstance(value, str):
            continue
        if key == "default" and value is None:
            continue
        if key == "description":
            if _param in CONVENTIONAL_PARAMS:
                continue
            short = first_sentence(value)
            if short:
                out["description"] = short
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                name: compact_schema(sub, _param=name) for name, sub in value.items()
            }
            continue
        out[key] = compact_schema(value)
    return out


def short_description(tool: Any) -> str:
    """The ``@cad_tool`` summary, else the docstring's first sentence."""
    cad = (getattr(tool, "meta", None) or {}).get("cad") or {}
    summary = cad.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return first_sentence(getattr(tool, "description", None), 200)


def _compact_tool(tool: Any) -> Any:
    meta = dict(getattr(tool, "meta", None) or {})
    # The search vocabulary is read server-side by the search transform from the
    # registered tool, never from a listing; the cost stays (one word).
    cad = meta.pop("cad", None)
    if isinstance(cad, dict) and cad.get("cost"):
        meta["cad"] = {"cost": cad["cost"]}
    update: dict[str, Any] = {
        "description": short_description(tool),
        "parameters": compact_schema(tool.parameters),
        "meta": meta or None,
    }
    return tool.model_copy(update=update)


_transform_cls = None


def make_compact_transform() -> Any:
    """Build the listing transform (fastmcp imported here, not at module scope)."""
    global _transform_cls
    if _transform_cls is None:
        from fastmcp.server.transforms import Transform

        class CompactListingTransform(Transform):
            """Rewrite ``tools/list`` entries; leave ``get_tool`` (the call path) alone."""

            async def list_tools(self, tools):  # type: ignore[override]
                return [_compact_tool(tool) for tool in tools]

        _transform_cls = CompactListingTransform
    return _transform_cls()
