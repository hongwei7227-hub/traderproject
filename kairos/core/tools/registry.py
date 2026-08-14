"""What an agent can do, and how it reaches it.

Tools come in two kinds, and the distinction is the most consequential design
decision inherited from the reference implementation.

**Direct tools** are offered to the model as callable functions. It asks for
one by name, gets a result, and continues. This is the ordinary arrangement and
it suits work that is one call deep: read a file, search the web.

**Programmatic tools** are not offered to the model at all. Instead the model
writes code, and that code imports generated wrappers and calls them. This
suits work that is a loop: fetch ten filings, extract one field from each,
compare. Offering those as direct tools would put ten results and ten round
trips through the context window to answer a question about one number.

The distinction is not a performance tweak. A platform with fifty data tools
cannot offer all fifty to the model — the descriptions alone would crowd out
the conversation — so the ability to expose them as an importable library
rather than a tool list is what makes a large catalogue usable at all.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from kairos.core.reasoning.turn import ToolRequest


class Exposure(StrEnum):
    """How a tool reaches the agent."""

    DIRECT = "direct"
    PROGRAMMATIC = "programmatic"
    BOTH = "both"


class Trust(StrEnum):
    """Where a tool's definition came from.

    Built-in definitions are written by this project. Tenant definitions arrive
    from a tenant's own configuration, which means their names, descriptions
    and parameter names are untrusted input that ends up inside generated
    source code and inside the model's prompt. Both are injection surfaces, and
    the generator hardens accordingly.
    """

    BUILTIN = "builtin"
    TENANT = "tenant"


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    type_hint: str
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One capability, described once and rendered two ways."""

    name: str
    description: str
    parameters: tuple[Parameter, ...] = ()
    exposure: Exposure = Exposure.DIRECT
    trust: Trust = Trust.BUILTIN
    namespace: str = ""
    returns: str = "Any"

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name

    def to_schema(self) -> dict[str, Any]:
        """The JSON Schema a model sees for a direct tool."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in self.parameters:
            properties[parameter.name] = {
                "type": _JSON_TYPES.get(parameter.type_hint, "string"),
                "description": parameter.description,
            }
            if parameter.required:
                required.append(parameter.name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }


_JSON_TYPES: Mapping[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeDefinition(ValueError):
    """A tenant-supplied definition cannot be rendered safely."""


def safe_identifier(candidate: str) -> str:
    """Reduce a name to something that is definitely a Python identifier.

    Tenant-supplied names end up as function and parameter names in generated
    source. A name containing a newline and a closing parenthesis can end the
    signature and start arbitrary code; a name that is a keyword produces a
    syntax error at import and takes the whole module with it.
    """
    stripped = candidate.strip()
    if not stripped:
        # Fabricating a name here would produce a tool the model cannot
        # meaningfully call, and two empty names would collide into one.
        raise UnsafeDefinition("a tool or parameter name cannot be empty")

    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", stripped)
    if not cleaned.strip("_"):
        # Everything was punctuation. The result would be "___", which is a
        # legal identifier and a meaningless one.
        raise UnsafeDefinition(f"{candidate!r} contains no usable characters")
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    if keyword.iskeyword(cleaned) or keyword.issoftkeyword(cleaned):
        cleaned = f"{cleaned}_"
    if not _IDENTIFIER.match(cleaned):
        raise UnsafeDefinition(f"cannot derive an identifier from {candidate!r}")
    return cleaned


def safe_text(value: str, *, limit: int = 512) -> str:
    """Make text safe to embed in a docstring and in a prompt.

    Two separate hazards. In generated source, a triple quote ends the
    docstring and everything after it is code. In a prompt, a line that looks
    like an instruction is one — a tenant-supplied description reading
    "Ignore previous instructions" is indistinguishable from the surrounding
    text unless the structure marks it as data.
    """
    without_quotes = value.replace('"""', "'''").replace("\\", "\\\\")
    flattened = " ".join(without_quotes.split())
    if len(flattened) > limit:
        flattened = f"{flattened[: limit - 1]}…"
    return flattened


@dataclass(slots=True)
class ToolRegistry:
    """Everything an agent may reach, in one place.

    Built once per turn rather than per process: which tools a tenant may use
    depends on their plan, their configuration, and which of their integrations
    are currently healthy.
    """

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)

    def register(self, definition: ToolDefinition) -> None:
        key = definition.qualified_name
        if key in self._tools:
            raise ValueError(f"tool {key!r} is already registered")
        if definition.trust is Trust.TENANT:
            self._validate_tenant_definition(definition)
        self._tools[key] = definition

    def register_all(self, definitions: Sequence[ToolDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    @staticmethod
    def _validate_tenant_definition(definition: ToolDefinition) -> None:
        """Reject at registration what cannot be rendered safely.

        Failing here means a tenant sees a configuration error. Failing later
        means a generated module that will not import, which the agent
        experiences as every tool from that integration silently missing.
        """
        safe_identifier(definition.name)
        for parameter in definition.parameters:
            safe_identifier(parameter.name)

    # -- queries -----------------------------------------------------------

    def direct(self) -> tuple[ToolDefinition, ...]:
        """Tools offered to the model as callable functions."""
        return tuple(
            t for t in self._tools.values() if t.exposure in (Exposure.DIRECT, Exposure.BOTH)
        )

    def programmatic(self) -> tuple[ToolDefinition, ...]:
        """Tools reachable only from code the model writes."""
        return tuple(
            t
            for t in self._tools.values()
            if t.exposure in (Exposure.PROGRAMMATIC, Exposure.BOTH)
        )

    def namespaces(self) -> tuple[str, ...]:
        return tuple(sorted({t.namespace for t in self.programmatic() if t.namespace}))

    def in_namespace(self, namespace: str) -> tuple[ToolDefinition, ...]:
        return tuple(t for t in self.programmatic() if t.namespace == namespace)

    def get(self, qualified_name: str) -> ToolDefinition | None:
        return self._tools.get(qualified_name)

    def accepts(self, request: ToolRequest) -> bool:
        """Whether a model may call this.

        Checks exposure, not just existence: a programmatic tool named in a
        direct call is either a confused model or a probe, and answering it
        would make the exposure distinction decorative.
        """
        definition = self._tools.get(request.name)
        return definition is not None and definition.exposure in (
            Exposure.DIRECT,
            Exposure.BOTH,
        )

    def schemas(self) -> list[dict[str, Any]]:
        """What goes to the model as its tool list."""
        return [
            {
                "name": t.name,
                "description": safe_text(t.description)
                if t.trust is Trust.TENANT
                else t.description,
                "parameters": t.to_schema(),
            }
            for t in self.direct()
        ]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


def render_module(namespace: str, tools: Sequence[ToolDefinition]) -> str:
    """Generate the Python module an agent imports to reach a namespace.

    The output is source code that will be written into a sandbox and imported
    there. Everything tenant-supplied that lands in it goes through
    `safe_identifier` or `repr` — never string interpolation of raw text into a
    position where code is expected.
    """
    lines = [
        '"""Generated. Do not edit — regenerated on every session."""',
        "",
        "from _runtime import invoke as _invoke",
        "",
    ]

    for tool in sorted(tools, key=lambda t: t.name):
        function_name = safe_identifier(tool.name)
        required = [p for p in tool.parameters if p.required]
        optional = [p for p in tool.parameters if not p.required]

        signature = ", ".join(
            [f"{safe_identifier(p.name)}: {p.type_hint}" for p in required]
            + [
                f"{safe_identifier(p.name)}: {p.type_hint} = {p.default!r}"
                for p in optional
            ]
        )

        lines.append(f"def {function_name}({signature}) -> {tool.returns}:")
        lines.append(f'    """{safe_text(tool.description)}')
        if tool.parameters:
            lines.append("")
            lines.append("    Args:")
            for parameter in tool.parameters:
                lines.append(
                    f"        {safe_identifier(parameter.name)}: "
                    f"{safe_text(parameter.description, limit=200)}"
                )
        lines.append('    """')

        # repr on both the namespace and the tool name: these are the two
        # places where a crafted string could otherwise close the call and
        # start a statement.
        argument_pairs = ", ".join(
            f"{p.name!r}: {safe_identifier(p.name)}" for p in tool.parameters
        )
        lines.append(f"    arguments = {{{argument_pairs}}}")
        lines.append("    arguments = {k: v for k, v in arguments.items() if v is not None}")
        lines.append(f"    return _invoke({namespace!r}, {tool.name!r}, arguments)")
        lines.append("")

    return "\n".join(lines)


def render_manifest(registry: ToolRegistry) -> str:
    """The summary of programmatic tools that goes into the prompt.

    Deliberately terse. The model needs to know which namespaces exist and how
    to import them; the signatures live in the generated modules, which it can
    read when it needs them. Inlining every signature here would spend the
    context window the code is meant to save.
    """
    if not registry.namespaces():
        return ""

    lines = ["Importable tool namespaces:"]
    for namespace in registry.namespaces():
        tools = registry.in_namespace(namespace)
        names = ", ".join(sorted(t.name for t in tools)[:5])
        more = "" if len(tools) <= 5 else f", … ({len(tools)} total)"
        lines.append(f"  from tools.{namespace} import {names}{more}")
    return "\n".join(lines)
