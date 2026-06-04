from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from molecule_ranker import __version__
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.registry import hash_manifest
from molecule_ranker.tool_ecosystem.schemas import (
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolRiskLevel,
    ToolSecurityScan,
    ToolSecurityScanStatus,
)

FindingSeverity = Literal["low", "medium", "high", "critical"]

SECRET_PATH_PATTERN = re.compile(
    r"(?:^|[/_.-])(?:secret|credential|token|apikey|api_key|key)(?:$|[/_.-])",
    re.I,
)
ENV_PATH_PATTERN = re.compile(r"(?:^|/)\.env(?:$|[./_-])", re.I)
CACHE_PATH_PATTERN = re.compile(
    r"(?:^|/)(?:\.cache|cache|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)(?:$|/)",
    re.I,
)
PRIVATE_HOST_PATTERN = re.compile(
    r"^(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|169\.254\.|::1|fc|fd)",
    re.I,
)
PROMPT_UNSAFE_PATTERN = re.compile(
    r"\b(?:ignore (?:all )?(?:previous|prior) instructions|"
    r"bypass (?:policy|guardrails|approval)|"
    r"disable (?:guardrails|validation)|"
    r"reveal (?:secrets|credentials)|"
    r"exfiltrate|"
    r"jailbreak)\b",
    re.I,
)
PROTOCOL_PROMPT_PATTERN = re.compile(
    r"\b(?:medical advice|treatment guidance|dose|dosing|dosage|mg/kg|mg/day|"
    r"synthesis route|retrosynthesis|lab protocol|wet[- ]lab protocol|"
    r"step[- ]by[- ]step (?:assay|protocol|synthesis)|"
    r"incubat(?:e|ion)|reagent concentration)\b",
    re.I,
)
BIOMEDICAL_OUTPUT_KEYS = {
    "evidence": {"evidence_item", "evidenceitem", "evidence_items"},
    "assay": {"assay_result", "assay_results", "assayresult"},
    "generated": {"generated_molecule", "generated_molecules", "generatedmolecule"},
}


class ToolSecurityFinding(BaseModel):
    finding_id: str
    severity: FindingSeverity
    message: str
    tool_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSecurityScannerConfig(BaseModel):
    admin_approved_network: bool = False
    admin_approved_shell: bool = False
    admin_approved_broad_filesystem: bool = False
    admin_approved_external_registry: bool = False


def scan_tool_package(
    package: ToolPackage | dict[str, Any],
    manifest: ToolManifest | dict[str, Any],
    *,
    config: ToolSecurityScannerConfig | None = None,
    scanner_version: str | None = None,
) -> ToolSecurityScan:
    findings: list[ToolSecurityFinding] = []
    parsed_package = _parse_package(package, findings)
    parsed_manifest = _parse_manifest(manifest, findings)
    active_config = config or ToolSecurityScannerConfig()
    if parsed_package is not None and parsed_manifest is not None:
        findings.extend(_scan_manifest_consistency(parsed_package, parsed_manifest))
        findings.extend(_scan_access_requests(parsed_manifest, active_config))
        findings.extend(_scan_runtime_tools(parsed_manifest, active_config))
        findings.extend(_scan_prompt_templates(parsed_manifest))
        findings.extend(_scan_dependency_metadata(parsed_manifest))

    risk_level = _risk_level(findings)
    status = _scan_status(findings)
    package_id = (
        parsed_package.package_id
        if parsed_package is not None
        else _raw_string(package, "package_id", "unknown-package")
    )
    package_version = (
        parsed_package.version
        if parsed_package is not None
        else _raw_string(package, "version", "unknown-version")
    )
    return ToolSecurityScan(
        scan_id=f"tool-scan-{uuid4().hex[:12]}",
        package_id=package_id,
        package_version=package_version,
        status=status,
        findings=[finding.model_dump(mode="json") for finding in findings],
        risk_level=risk_level,
        scanned_at=datetime.now(UTC),
        scanner_version=scanner_version or f"molecule-ranker-security-{__version__}",
        metadata={
            "scanner": "tool_ecosystem_static_security",
            "critical_findings_block_approval": True,
        },
    )


def has_blocking_findings(scan: ToolSecurityScan) -> bool:
    return any(finding.get("severity") == "critical" for finding in scan.findings)


def _parse_package(
    package: ToolPackage | dict[str, Any],
    findings: list[ToolSecurityFinding],
) -> ToolPackage | None:
    if isinstance(package, ToolPackage):
        return package
    try:
        return ToolPackage.model_validate(package)
    except ValidationError as exc:
        findings.append(
            _finding(
                "package_schema_invalid",
                "critical",
                "Tool package schema is invalid.",
                errors=exc.errors(include_url=False),
            )
        )
        return None


def _parse_manifest(
    manifest: ToolManifest | dict[str, Any],
    findings: list[ToolSecurityFinding],
) -> ToolManifest | None:
    if isinstance(manifest, ToolManifest):
        return manifest
    try:
        return ToolManifest.model_validate(manifest)
    except ValidationError as exc:
        findings.append(
            _finding(
                "manifest_schema_invalid",
                "critical",
                "Tool manifest schema is invalid.",
                errors=exc.errors(include_url=False),
            )
        )
        return None


def _scan_manifest_consistency(
    package: ToolPackage,
    manifest: ToolManifest,
) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    if package.package_id != manifest.package_id:
        findings.append(
            _finding(
                "manifest_package_id_mismatch",
                "critical",
                "Manifest package_id does not match package.",
            )
        )
    if package.version != manifest.package_version:
        findings.append(
            _finding(
                "manifest_version_mismatch",
                "critical",
                "Manifest package_version does not match package.",
            )
        )
    if package.manifest_hash and package.manifest_hash != hash_manifest(manifest):
        findings.append(
            _finding(
                "manifest_hash_mismatch",
                "critical",
                "Manifest hash does not match package metadata.",
            )
        )
    return findings


def _scan_access_requests(
    manifest: ToolManifest,
    config: ToolSecurityScannerConfig,
) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    for request in manifest.requested_filesystem_access:
        path = str(request.get("path") or request.get("root") or "")
        mode = str(request.get("mode") or request.get("access") or "").lower()
        if _is_secret_path(path):
            findings.append(
                _finding(
                    "secret_path_access",
                    "critical",
                    "Tool package requests access to a secret or credential path.",
                    path=path,
                    request=request,
                )
            )
        if ENV_PATH_PATTERN.search(path):
            findings.append(
                _finding(
                    "env_file_access",
                    "critical",
                    "Tool package requests .env file access.",
                    path=path,
                    request=request,
                )
            )
        if CACHE_PATH_PATTERN.search(path):
            findings.append(
                _finding(
                    "cache_path_access",
                    "high",
                    "Tool package requests cache file access.",
                    path=path,
                    request=request,
                )
            )
        if _is_broad_filesystem_path(path) and not config.admin_approved_broad_filesystem:
            findings.append(
                _finding(
                    "excessive_filesystem_access",
                    "high",
                    "Tool package requests excessive filesystem access.",
                    path=path,
                    mode=mode,
                    request=request,
                )
            )
        if "write" in mode and _is_broad_filesystem_path(path):
            findings.append(
                _finding(
                    "broad_filesystem_write",
                    "critical",
                    "Tool package requests broad filesystem write access.",
                    path=path,
                    request=request,
                )
            )
    for env_var in manifest.requested_environment_variables:
        severity: FindingSeverity = "critical" if _looks_secret(env_var) else "high"
        findings.append(
            _finding(
                "environment_variable_request",
                severity,
                "Tool package requests environment variables.",
                environment_variable=env_var,
            )
        )
    for request in manifest.requested_network_access:
        findings.extend(_scan_network_request(request, config))
    for domain in manifest.external_domains:
        findings.extend(_scan_domain(domain, config))
    return findings


def _scan_runtime_tools(
    manifest: ToolManifest,
    config: ToolSecurityScannerConfig,
) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    for spec in manifest.tools:
        findings.extend(_scan_tool_schema(spec))
        findings.extend(_scan_tool_side_effects(spec, config))
        findings.extend(_scan_biomedical_outputs(spec))
        findings.extend(_scan_tool_prompt_metadata(spec))
    return findings


def _scan_tool_schema(spec: RuntimeToolSpec) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    if spec.input_schema.get("type") != "object":
        findings.append(
            _finding(
                "tool_input_schema_invalid",
                "critical",
                "Tool input schema must be a JSON object schema.",
                tool_name=spec.tool_name,
            )
        )
    if spec.output_schema.get("type") != "object":
        findings.append(
            _finding(
                "tool_output_schema_invalid",
                "critical",
                "Tool output schema must be a JSON object schema.",
                tool_name=spec.tool_name,
            )
        )
    try:
        json.dumps(spec.input_schema, sort_keys=True)
        json.dumps(spec.output_schema, sort_keys=True)
    except TypeError as exc:
        findings.append(
            _finding(
                "tool_schema_not_json_serializable",
                "critical",
                "Tool schemas must be JSON serializable.",
                tool_name=spec.tool_name,
                error=str(exc),
            )
        )
    return findings


def _scan_tool_side_effects(
    spec: RuntimeToolSpec,
    config: ToolSecurityScannerConfig,
) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    if spec.side_effect_level == "external_write" and not spec.requires_approval_by_default:
        findings.append(
            _finding(
                "external_write_without_approval",
                "critical",
                "External write tools must require approval by default.",
                tool_name=spec.tool_name,
            )
        )
    shell_markers = _tool_shell_markers(spec)
    if shell_markers and spec.side_effect_level != "codex_subprocess":
        findings.append(
            _finding(
                "shell_execution_not_classified",
                "critical",
                "Shell execution must be classified as codex_subprocess.",
                tool_name=spec.tool_name,
                markers=shell_markers,
            )
        )
    if (
        spec.side_effect_level == "codex_subprocess"
        or "shell_execution" in spec.policy_tags
        or shell_markers
    ) and not config.admin_approved_shell:
        findings.append(
            _finding(
                "shell_execution_not_admin_approved",
                "critical",
                "Shell execution requires explicit admin approval.",
                tool_name=spec.tool_name,
                markers=shell_markers,
            )
        )
    return findings


def _scan_biomedical_outputs(spec: RuntimeToolSpec) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    keys = _schema_keys(spec.output_schema)
    policy_tags = set(spec.policy_tags)
    metadata_text = _json_text(spec.metadata)
    if keys.intersection(BIOMEDICAL_OUTPUT_KEYS["evidence"]) or "EvidenceItem" in metadata_text:
        if "evidence_import_schema_validated" not in policy_tags:
            findings.append(
                _finding(
                    "evidence_creation_without_validator",
                    "critical",
                    "EvidenceItem creation requires an evidence importer validator.",
                    tool_name=spec.tool_name,
                )
            )
    if keys.intersection(BIOMEDICAL_OUTPUT_KEYS["assay"]) or re.search(
        r"\bAssayResult\b|\bassay[_ -]?results?\b", metadata_text, re.I
    ):
        if "experimental_import_schema_validated" not in policy_tags:
            findings.append(
                _finding(
                    "assay_result_creation_without_validator",
                    "critical",
                    "Assay result creation requires an experimental importer validator.",
                    tool_name=spec.tool_name,
                )
            )
    if keys.intersection(BIOMEDICAL_OUTPUT_KEYS["generated"]) or re.search(
        r"\bgenerated[_ -]?molecules?\b", metadata_text, re.I
    ):
        if "generation_pipeline_schema_validated" not in policy_tags:
            findings.append(
                _finding(
                    "generated_molecule_creation_outside_pipeline",
                    "critical",
                    "Generated molecule creation requires the validated generation pipeline.",
                    tool_name=spec.tool_name,
                )
            )
    return findings


def _scan_tool_prompt_metadata(spec: RuntimeToolSpec) -> list[ToolSecurityFinding]:
    text = _json_text(
        {
            "description": spec.description,
            "metadata": spec.metadata,
            "policy_tags": spec.policy_tags,
        }
    )
    return _prompt_findings(text, tool_name=spec.tool_name)


def _scan_prompt_templates(manifest: ToolManifest) -> list[ToolSecurityFinding]:
    text = _json_text(
        {
            "skills": manifest.skills,
            "workflows": manifest.workflows,
            "metadata": manifest.metadata,
        }
    )
    return _prompt_findings(text, tool_name=None)


def _scan_dependency_metadata(manifest: ToolManifest) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    dependencies = manifest.metadata.get("dependencies")
    if isinstance(dependencies, list) and dependencies:
        findings.append(
            _finding(
                "dependency_metadata_review",
                "medium",
                "Package dependency metadata requires review.",
                dependencies=dependencies,
            )
        )
    license_name = manifest.license or manifest.metadata.get("license")
    if isinstance(license_name, str) and license_name.lower() in {
        "gpl",
        "gpl-2.0",
        "gpl-3.0",
        "agpl",
        "agpl-3.0",
        "unknown",
    }:
        findings.append(
            _finding(
                "license_review",
                "medium",
                "Package license metadata requires legal review.",
                license=license_name,
            )
        )
    return findings


def _scan_network_request(
    request: dict[str, Any],
    config: ToolSecurityScannerConfig,
) -> list[ToolSecurityFinding]:
    values = [
        str(request.get(key) or "")
        for key in ("domain", "host", "url", "cidr", "pattern")
        if request.get(key) is not None
    ]
    findings: list[ToolSecurityFinding] = []
    for value in values:
        findings.extend(_scan_domain(value, config, request=request))
    return findings


def _scan_domain(
    domain: str,
    config: ToolSecurityScannerConfig,
    *,
    request: dict[str, Any] | None = None,
) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    normalized = domain.strip().lower()
    if not normalized:
        return findings
    if _is_wildcard_network(normalized):
        severity: FindingSeverity = "high" if config.admin_approved_network else "critical"
        findings.append(
            _finding(
                "broad_network_wildcard",
                severity,
                "Broad network wildcard requires admin approval.",
                domain=domain,
                request=request,
            )
        )
    host = _domain_host(normalized)
    if PRIVATE_HOST_PATTERN.match(host):
        severity = "high" if config.admin_approved_network else "critical"
        findings.append(
            _finding(
                "local_private_network_access",
                severity,
                "Local or private network access requires admin approval.",
                domain=domain,
                request=request,
            )
        )
    return findings


def _prompt_findings(text: str, *, tool_name: str | None) -> list[ToolSecurityFinding]:
    findings: list[ToolSecurityFinding] = []
    if PROTOCOL_PROMPT_PATTERN.search(text):
        findings.append(
            _finding(
                "forbidden_biomedical_prompt_template",
                "critical",
                "Prompt/template text contains medical, synthesis, protocol, or dosing content.",
                tool_name=tool_name,
            )
        )
    if PROMPT_UNSAFE_PATTERN.search(text):
        findings.append(
            _finding(
                "unsafe_codex_prompt_template",
                "critical",
                "Prompt/template text attempts to bypass policy, secrets, or guardrails.",
                tool_name=tool_name,
            )
        )
    return findings


def _finding(
    finding_id: str,
    severity: FindingSeverity,
    message: str,
    *,
    tool_name: str | None = None,
    **metadata: Any,
) -> ToolSecurityFinding:
    return ToolSecurityFinding(
        finding_id=finding_id,
        severity=severity,
        message=message,
        tool_name=tool_name,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def _risk_level(findings: list[ToolSecurityFinding]) -> ToolRiskLevel:
    severities = {finding.severity for finding in findings}
    if "critical" in severities:
        return "critical"
    if "high" in severities:
        return "high"
    if severities.intersection({"medium", "low"}):
        return "medium"
    return "low"


def _scan_status(findings: list[ToolSecurityFinding]) -> ToolSecurityScanStatus:
    severities = {finding.severity for finding in findings}
    if "critical" in severities:
        return "failed"
    if severities:
        return "warning"
    return "passed"


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
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            if isinstance(item, dict):
                keys.update(_schema_keys(item))
    return keys


def _tool_shell_markers(spec: RuntimeToolSpec) -> list[str]:
    text = _json_text(spec.metadata)
    markers = []
    for marker in ("shell", "subprocess", "bash", "sh -c", "command", "cli"):
        if marker in text.lower():
            markers.append(marker)
    return markers


def _is_secret_path(path: str) -> bool:
    return bool(SECRET_PATH_PATTERN.search(path) or _looks_secret(path))


def _looks_secret(value: str) -> bool:
    return bool(re.search(r"(?:secret|credential|token|api[_-]?key|private[_-]?key)", value, re.I))


def _is_broad_filesystem_path(path: str) -> bool:
    normalized = path.strip()
    return normalized in {"", "/", "~", ".", "*", "/tmp", "/var", "/Users", "/home"}


def _is_wildcard_network(value: str) -> bool:
    return (
        value in {"*", "*.*", "0.0.0.0/0", "::/0"}
        or value.startswith("*.")
        or value.endswith("/*")
    )


def _domain_host(value: str) -> str:
    without_scheme = value.split("://", maxsplit=1)[-1]
    return without_scheme.split("/", maxsplit=1)[0].split(":", maxsplit=1)[0]


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _raw_string(value: Any, key: str, default: str) -> str:
    if isinstance(value, dict) and isinstance(value.get(key), str):
        return str(value[key])
    return default


__all__ = [
    "ToolApproval",
    "ToolSecurityFinding",
    "ToolSecurityScan",
    "ToolSecurityScannerConfig",
    "has_blocking_findings",
    "scan_tool_package",
]
