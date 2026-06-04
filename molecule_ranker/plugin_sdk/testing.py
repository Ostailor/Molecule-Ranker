from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.plugin_sdk.manifest import PluginPackageBundle


class PluginTestResult(BaseModel):
    passed: bool
    errors: list[str] = Field(default_factory=list)
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)


def run_plugin_tests(
    bundle: PluginPackageBundle,
    *,
    sample_inputs: dict[str, dict[str, Any]] | None = None,
) -> PluginTestResult:
    validation = bundle.validate_package()
    errors = list(validation.errors)
    outputs: dict[str, dict[str, Any]] = {}
    if errors:
        return PluginTestResult(passed=False, errors=errors, outputs=outputs)
    samples = sample_inputs or {}
    for tool in bundle.tools:
        try:
            raw = tool.handler(samples.get(tool.name, {}))
        except Exception as exc:
            errors.append(f"{tool.name} handler failed: {exc}")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{tool.name} handler must return a dict")
            continue
        outputs[tool.name] = raw
    return PluginTestResult(passed=not errors, errors=errors, outputs=outputs)


__all__ = ["PluginTestResult", "run_plugin_tests"]
