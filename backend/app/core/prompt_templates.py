from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from jinja2 import Environment, StrictUndefined


Resolver = Callable[[], Any]


@dataclass(slots=True)
class TemplateVariable:
    name: str
    description: str
    resolver: Resolver


class PromptTemplateRegistry:
    """Thin Jinja2 wrapper used by Analyze/Rewrite stages."""

    def __init__(self) -> None:
        self._environment = Environment(
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )
        self._environment.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        self._variables: dict[str, TemplateVariable] = {}
        self._register_builtin_variables()

    def _register_builtin_variables(self) -> None:
        self.register("now_iso", "Current UTC timestamp in ISO8601", lambda: datetime.now(timezone.utc).isoformat())

    def register(self, name: str, description: str, resolver: Resolver) -> None:
        self._variables[name] = TemplateVariable(name=name, description=description, resolver=resolver)

    def registered_variables(self) -> dict[str, str]:
        return {name: variable.description for name, variable in self._variables.items()}

    def render(self, template: str, context: dict[str, Any] | None = None) -> str:
        payload = {name: variable.resolver() for name, variable in self._variables.items()}
        if context:
            payload.update(context)
        compiled = self._environment.from_string(template)
        return compiled.render(**payload)
