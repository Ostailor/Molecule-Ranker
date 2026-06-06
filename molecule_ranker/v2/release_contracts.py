from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

V2_SCHEMA_VERSION = "2.7"
V2_CONTRACT_VERSION = "v2.7.0"
V2_API_CONTRACT_VERSION = "api.v2"

ContractKind = Literal[
    "api",
    "artifact",
    "cli",
    "database",
    "schema",
]
StabilityLevel = Literal["stable", "supported_deprecated"]


@dataclass(frozen=True)
class V2ReleaseContract:
    contract_id: str
    kind: ContractKind
    schema_version: str
    contract_version: str
    stability: StabilityLevel
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    breaking_changes: tuple[str, ...] = ()
    deprecation_notes: tuple[str, ...] = ()
    compatibility_notes: str = ""

    @property
    def breaking_changes_documented(self) -> bool:
        return bool(self.breaking_changes) or "no breaking" in self.compatibility_notes.lower()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required_fields"] = list(self.required_fields)
        payload["optional_fields"] = list(self.optional_fields)
        payload["breaking_changes"] = list(self.breaking_changes)
        payload["deprecation_notes"] = list(self.deprecation_notes)
        payload["breaking_changes_documented"] = self.breaking_changes_documented
        return payload


@dataclass(frozen=True)
class V2ArtifactSchemaContract:
    artifact_type: str
    schema_version: str
    contract_version: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    compatibility_notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "compatibility_notes": self.compatibility_notes,
        }


def _release_contract(
    contract_id: str,
    kind: ContractKind,
    required_fields: tuple[str, ...],
    *,
    optional_fields: tuple[str, ...] = (),
    stability: StabilityLevel = "stable",
    breaking_changes: tuple[str, ...] = (),
    deprecation_notes: tuple[str, ...] = (),
    compatibility_notes: str = "No breaking changes from the V2.0 frozen contract.",
) -> V2ReleaseContract:
    return V2ReleaseContract(
        contract_id=contract_id,
        kind=kind,
        schema_version=V2_SCHEMA_VERSION,
        contract_version=V2_CONTRACT_VERSION,
        stability=stability,
        required_fields=required_fields,
        optional_fields=optional_fields,
        breaking_changes=breaking_changes,
        deprecation_notes=deprecation_notes,
        compatibility_notes=compatibility_notes,
    )


def _artifact_schema(
    artifact_type: str,
    required_fields: tuple[str, ...],
    optional_fields: tuple[str, ...] = (),
    compatibility_notes: str = "V2.0 artifacts require schema_version and contract_version.",
) -> V2ArtifactSchemaContract:
    return V2ArtifactSchemaContract(
        artifact_type=artifact_type,
        schema_version=V2_SCHEMA_VERSION,
        contract_version=V2_CONTRACT_VERSION,
        required_fields=("schema_version", "contract_version", *required_fields),
        optional_fields=optional_fields,
        compatibility_notes=compatibility_notes,
    )


V2_API_ROUTES: tuple[str, ...] = (
    "/api/v2/health",
    "/api/v2/ready",
    "/api/v2/version",
    "/api/v2/projects",
    "/api/v2/projects/{project_id}",
    "/api/v2/projects/{project_id}/artifacts",
    "/api/v2/review/health",
    "/api/v2/experiments/health",
    "/api/v2/e2e/workflows",
    "/api/v2/e2e/workflows/{id}",
    "/api/v2/e2e/workflows/{id}/resume",
    "/api/v2/e2e/workflows/{id}/cancel",
    "/api/v2/e2e/workflows/{id}/lineage",
    "/api/v2/e2e/workflows/{id}/bundle",
    "/api/v2/e2e/workflows/{id}/validate",
    "/api/v2/integrations/catalog",
    "/api/v2/integrations/operations/dashboard",
    "/api/v2/jobs/{job_id}",
    "/api/v2/projects/{project_id}/codex/summarize",
    "/api/v2/admin/health",
    "/api/v2/admin/audit",
)

V2_CLI_COMMAND_GROUPS: tuple[str, ...] = (
    "v2",
    "api",
    "release",
    "validate",
    "db",
    "project",
    "review",
    "experiment",
    "campaign",
    "eval",
    "e2e",
    "integration",
    "codex",
    "admin",
)

V2_DATABASE_SCHEMA_VERSION = "2026_05_27_0001_platform_core"

V2_ARTIFACT_SCHEMAS: dict[str, V2ArtifactSchemaContract] = {
    "model_card": _artifact_schema(
        "model_card",
        ("model_id", "endpoint", "training_manifest", "metrics", "limitations"),
        ("calibration_metrics", "applicability_domain", "registry_metadata"),
    ),
    "generated_molecule": _artifact_schema(
        "generated_molecule",
        (
            "generated_molecule_id",
            "smiles",
            "generation_method",
            "hypothesis_only",
            "evidence_boundary",
        ),
        ("parent_candidate_id", "score_breakdown", "developability_summary", "warnings"),
    ),
    "evidence_item": _artifact_schema(
        "evidence_item",
        ("evidence_id", "source_type", "source_id", "claim", "provenance"),
        ("confidence", "limitations", "linked_candidate_ids"),
    ),
    "review_workspace": _artifact_schema(
        "review_workspace",
        ("workspace_id", "review_items", "audit_events"),
        ("summary", "assignments", "comments"),
    ),
    "campaign": _artifact_schema(
        "campaign",
        ("campaign_id", "objectives", "work_packages", "stage_gates", "audit_trail"),
        ("budget", "scenario_results", "decision_memos"),
    ),
    "evaluation": _artifact_schema(
        "evaluation",
        ("report_id", "suite_id", "metrics", "limitations", "reproducibility"),
        ("dataset_id", "split_id", "guardrail_results"),
    ),
    "integration_sync": _artifact_schema(
        "integration_sync",
        ("sync_job", "records", "contract_report"),
        ("mapping_report", "artifact_manifest", "webhook_events"),
    ),
    "codex_task_result": _artifact_schema(
        "codex_task_result",
        ("task_id", "task_type", "status", "guardrail_status", "artifact_context"),
        ("summary", "result", "redactions", "warnings"),
    ),
    "knowledge_graph": _artifact_schema(
        "knowledge_graph",
        ("graph_id", "entities", "relations", "provenance"),
        ("queries", "contradictions", "staleness_report"),
    ),
    "end_to_end_result_bundle": _artifact_schema(
        "end_to_end_result_bundle",
        (
            "workflow_id",
            "mode",
            "status",
            "workflow_state",
            "runtime_plan",
            "sync_plan",
            "integration_result",
            "artifacts",
            "lineage_links",
            "safety_constraints",
        ),
        ("repair_plan", "audit_events", "warnings"),
        "V2.7 bundles must preserve workflow state, lineage, validation gates, "
        "and safety constraints.",
    ),
}

V2_RELEASE_CONTRACTS: tuple[V2ReleaseContract, ...] = (
    _release_contract(
        "api_routes",
        "api",
        ("contract_version", "schema_version", "routes"),
        breaking_changes=("V2.0 introduces /api/v2 as the stable enterprise API prefix.",),
        deprecation_notes=("/api/v1 remains supported with deprecation notes.",),
        compatibility_notes="Breaking API prefix change is documented; V1 remains supported.",
    ),
    _release_contract("artifact_schemas", "artifact", ("schema_version", "contract_version")),
    _release_contract("cli_command_groups", "cli", ("command_groups",)),
    _release_contract("database_schema_version", "database", ("schema_version",)),
    _release_contract(
        "model_card_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["model_card"].required_fields,
    ),
    _release_contract(
        "generated_molecule_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["generated_molecule"].required_fields,
    ),
    _release_contract(
        "evidence_item_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["evidence_item"].required_fields,
    ),
    _release_contract(
        "review_workspace_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["review_workspace"].required_fields,
    ),
    _release_contract("campaign_schema", "schema", V2_ARTIFACT_SCHEMAS["campaign"].required_fields),
    _release_contract(
        "evaluation_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["evaluation"].required_fields,
    ),
    _release_contract(
        "integration_sync_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["integration_sync"].required_fields,
    ),
    _release_contract(
        "codex_task_result_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["codex_task_result"].required_fields,
    ),
    _release_contract(
        "knowledge_graph_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["knowledge_graph"].required_fields,
    ),
    _release_contract(
        "end_to_end_result_bundle_schema",
        "schema",
        V2_ARTIFACT_SCHEMAS["end_to_end_result_bundle"].required_fields,
        compatibility_notes=(
            "No breaking changes from the V2 contract; V2.7 adds additive end-to-end "
            "workflow bundles without removing existing V2 API routes."
        ),
    ),
)


def list_v2_release_contracts() -> list[V2ReleaseContract]:
    return list(V2_RELEASE_CONTRACTS)
