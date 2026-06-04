from __future__ import annotations

from typing import Any

from molecule_ranker.plugin_sdk import (
    build_manifest,
    define_tool,
    define_workflow,
    run_plugin_tests,
    validate_package,
)
from molecule_ranker.plugin_sdk.examples import (
    build_example_plugin,
    example_artifact_metric_tool,
    example_noop_workflow,
    example_safe_summary_tool,
)

OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}


def test_sdk_builds_manifest() -> None:
    bundle = build_example_plugin()

    assert bundle.package.package_id == "example-safe-plugin"
    assert bundle.package.tool_count == 2
    assert bundle.package.workflow_count == 1
    assert {tool.tool_name for tool in bundle.manifest.tools} == {
        "plugin.example_safe_plugin.example_safe_summary_tool",
        "plugin.example_safe_plugin.example_artifact_metric_tool",
    }
    assert bundle.manifest.manifest_id == "example-safe-plugin-manifest"
    assert bundle.package.manifest_hash.startswith("sha256:")
    assert example_safe_summary_tool.name == "example_safe_summary_tool"
    assert example_artifact_metric_tool.name == "example_artifact_metric_tool"
    assert example_noop_workflow.name == "example_noop_workflow"


def test_plugin_package_validates_and_tests_run() -> None:
    bundle = build_example_plugin()

    validation = validate_package(bundle.package, bundle.manifest, tools=bundle.tools)
    test_result = run_plugin_tests(
        bundle,
        sample_inputs={
            "example_safe_summary_tool": {"text": "approved artifact text"},
            "example_artifact_metric_tool": {"values": [1, 2, 3]},
        },
    )

    assert validation.valid is True
    assert validation.errors == []
    assert test_result.passed is True
    assert test_result.outputs["example_artifact_metric_tool"]["value"] == 6


def test_unsafe_plugin_rejected() -> None:
    unsafe_tool = define_tool(
        "unsafe_tool",
        OBJECT_SCHEMA,
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "evidence_item": {"type": "object"},
                "assay_results": {"type": "array"},
            },
        },
        lambda payload: {"evidence_item": {"id": "fake"}},
        description="Unsafe tool.",
        network_access=[{"domain": "example.com"}],
        filesystem_access=[{"path": "/tmp"}],
    )
    unsafe_workflow = define_workflow(
        "unsafe_workflow",
        description="Unsafe workflow.",
        steps=[{"tool_name": "plugin.unsafe_plugin.unsafe_tool"}],
    )
    bundle = build_manifest(
        package_id="unsafe-plugin",
        name="unsafe_plugin",
        description="Unsafe package.",
        version="1.0.0",
        publisher="example",
        tools=[unsafe_tool],
        workflows=[unsafe_workflow],
    )

    validation = validate_package(bundle.package, bundle.manifest, tools=bundle.tools)
    test_result = run_plugin_tests(bundle)

    assert validation.valid is False
    assert any("filesystem" in error for error in validation.errors)
    assert any("network" in error for error in validation.errors)
    assert any("EvidenceItem" in error for error in validation.errors)
    assert any("assay results" in error for error in validation.errors)
    assert test_result.passed is False


def test_importer_plugins_require_validation_tags() -> None:
    evidence_importer = define_tool(
        "evidence_importer",
        OBJECT_SCHEMA,
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {"evidence_item": {"type": "object"}},
        },
        _handler,
        kind="evidence_importer",
    )
    assay_importer = define_tool(
        "assay_importer",
        OBJECT_SCHEMA,
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {"assay_results": {"type": "array"}},
        },
        _handler,
        kind="assay_importer",
    )
    generation_tool = define_tool(
        "generation_tool",
        OBJECT_SCHEMA,
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {"generated_molecules": {"type": "array"}},
        },
        _handler,
        kind="generation_pipeline",
    )
    bundle = build_manifest(
        package_id="importer-plugin",
        name="importer_plugin",
        description="Importer plugin.",
        version="1.0.0",
        publisher="example",
        tools=[evidence_importer, assay_importer, generation_tool],
    )

    validation = validate_package(bundle.package, bundle.manifest, tools=bundle.tools)

    assert validation.valid is False
    assert any("evidence importer lacks validation tag" in error for error in validation.errors)
    assert any("assay importer lacks experimental" in error for error in validation.errors)
    assert any("generation pipeline lacks generation" in error for error in validation.errors)


def _handler(payload: dict[str, Any]) -> dict[str, Any]:
    return {}
