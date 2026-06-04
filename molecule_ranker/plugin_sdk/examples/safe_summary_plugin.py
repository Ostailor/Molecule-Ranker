from __future__ import annotations

from typing import Any

from molecule_ranker.plugin_sdk.manifest import PluginPackageBundle, build_manifest
from molecule_ranker.plugin_sdk.tool import PluginTool, define_tool
from molecule_ranker.plugin_sdk.workflow import PluginWorkflow, define_workflow

OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}


def _summary_handler(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", ""))
    return {"summary": text[:200], "character_count": len(text)}


def _metric_handler(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values")
    numeric = [float(value) for value in values] if isinstance(values, list) else []
    total = sum(numeric)
    return {"metric_name": "sum", "value": total, "count": len(numeric)}


example_safe_summary_tool: PluginTool = define_tool(
    "example_safe_summary_tool",
    OBJECT_SCHEMA,
    {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "character_count": {"type": "integer"},
        },
    },
    _summary_handler,
    description="Summarize already-approved text without creating scientific evidence.",
    kind="summary",
    policy_tags=["no_evidence_creation"],
)

example_artifact_metric_tool: PluginTool = define_tool(
    "example_artifact_metric_tool",
    OBJECT_SCHEMA,
    {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "metric_name": {"type": "string"},
            "value": {"type": "number"},
            "count": {"type": "integer"},
        },
    },
    _metric_handler,
    description="Compute simple metrics over approved artifact values.",
    kind="analysis",
    policy_tags=["artifact_only"],
)

example_noop_workflow: PluginWorkflow = define_workflow(
    "example_noop_workflow",
    description="No-op workflow for validating plugin wiring.",
    steps=[
        {
            "tool_name": "plugin.example_safe_plugin.example_safe_summary_tool",
            "tool_args": {"text": "approved artifact text"},
        }
    ],
    required_tools=["plugin.example_safe_plugin.example_safe_summary_tool"],
    required_permissions=["plugin:example_safe_plugin:run"],
    expected_artifacts=[],
    forbidden_outputs=["EvidenceItem", "assay_results", "generated_molecules"],
)


def build_example_plugin() -> PluginPackageBundle:
    return build_manifest(
        package_id="example-safe-plugin",
        name="example_safe_plugin",
        display_name="Example Safe Plugin",
        description="Example safe plugin package for internal developers.",
        version="1.0.0",
        publisher="molecule-ranker",
        tools=[example_safe_summary_tool, example_artifact_metric_tool],
        workflows=[example_noop_workflow],
        metadata={"example": True},
    )


__all__ = [
    "build_example_plugin",
    "example_artifact_metric_tool",
    "example_noop_workflow",
    "example_safe_summary_tool",
]
