from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.plugin_sdk.tool import PluginTool
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage

BLOCKED_OUTPUT_KEYS = {
    "assay_result",
    "assay_results",
    "evidence_item",
    "evidenceitem",
    "generated_molecule",
    "generated_molecules",
}
BLOCKED_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bEvidenceItem\b|\bevidence_item\b", re.I), "EvidenceItem creation"),
    (re.compile(r"\bassay[_ -]?results?\b|\bAssayResult\b", re.I), "assay result creation"),
    (re.compile(r"\bgenerated[_ -]?molecules?\b", re.I), "generated molecule creation"),
)


class PluginValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_package(
    package: ToolPackage,
    manifest: ToolManifest,
    *,
    tools: list[PluginTool] | None = None,
) -> PluginValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if package.package_id != manifest.package_id:
        errors.append("Package id does not match manifest.")
    if package.version != manifest.package_version:
        errors.append("Package version does not match manifest.")
    if package.status == "approved":
        warnings.append("Plugin SDK packages should be submitted for approval, not self-approved.")
    plugin_tools = tools or []
    for tool in plugin_tools:
        errors.extend(_validate_tool_access(tool))
        errors.extend(_validate_biomedical_boundaries(tool))
    for spec in manifest.tools:
        metadata = spec.metadata.get("plugin_tool")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("filesystem_access"):
            errors.append(f"{spec.tool_name} requests filesystem access by default.")
        if metadata.get("network_access") or metadata.get("external_domains"):
            errors.append(f"{spec.tool_name} requests network access by default.")
    return PluginValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_tool_access(tool: PluginTool) -> list[str]:
    errors: list[str] = []
    if tool.filesystem_access:
        errors.append(f"{tool.name} cannot access arbitrary filesystem by default.")
    if tool.network_access or tool.external_domains:
        errors.append(f"{tool.name} cannot access network by default.")
    return errors


def _validate_biomedical_boundaries(tool: PluginTool) -> list[str]:
    errors: list[str] = []
    text = json.dumps(
        {
            "output_schema": tool.output_schema,
            "metadata": tool.metadata,
            "policy_tags": tool.policy_tags,
        },
        sort_keys=True,
        default=str,
    )
    output_keys = _schema_keys(tool.output_schema)
    if (
        output_keys.intersection({"evidence_item", "evidenceitem"})
        and tool.kind != "evidence_importer"
    ):
        errors.append(f"{tool.name} cannot create EvidenceItem records.")
    if (
        output_keys.intersection({"assay_result", "assay_results"})
        and tool.kind != "assay_importer"
    ):
        errors.append(f"{tool.name} cannot create assay results.")
    if output_keys.intersection({"generated_molecule", "generated_molecules"}) and (
        tool.kind != "generation_pipeline"
    ):
        errors.append(f"{tool.name} cannot create generated molecules.")
    if (
        tool.kind == "evidence_importer"
        and "evidence_import_schema_validated" not in tool.policy_tags
    ):
        errors.append(f"{tool.name} evidence importer lacks validation tag.")
    if (
        tool.kind == "assay_importer"
        and "experimental_import_schema_validated" not in tool.policy_tags
    ):
        errors.append(f"{tool.name} assay importer lacks experimental import validation tag.")
    if (
        tool.kind == "generation_pipeline"
        and "generation_pipeline_schema_validated" not in tool.policy_tags
    ):
        errors.append(f"{tool.name} generation pipeline lacks generation schema validation tag.")
    for pattern, label in BLOCKED_TEXT_PATTERNS:
        if pattern.search(text) and not _kind_allows_label(tool.kind, label):
            errors.append(f"{tool.name} attempts prohibited {label}.")
    return list(dict.fromkeys(errors))


def _schema_keys(schema: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        keys.update(str(key).lower().replace("-", "_") for key in properties)
        for value in properties.values():
            if isinstance(value, dict):
                keys.update(_schema_keys(value))
    items = schema.get("items")
    if isinstance(items, dict):
        keys.update(_schema_keys(items))
    return keys


def _kind_allows_label(kind: str, label: str) -> bool:
    return (
        (label == "EvidenceItem creation" and kind == "evidence_importer")
        or (label == "assay result creation" and kind == "assay_importer")
        or (label == "generated molecule creation" and kind == "generation_pipeline")
    )


__all__ = ["PluginValidationResult", "validate_package"]
