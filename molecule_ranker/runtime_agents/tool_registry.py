from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec

JSON_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
RUNTIME_EXECUTION_MODE = "delegate_to_existing_module_or_cli"


class RuntimeToolRegistry:
    """Approved runtime-agent tool catalog.

    The registry declares deterministic molecule-ranker tool surfaces. It does
    not implement tool logic; runtime executors must delegate to the existing
    module or CLI entrypoint recorded in each spec metadata.
    """

    def __init__(self, specs: Iterable[RuntimeToolSpec] | None = None) -> None:
        self._specs: dict[str, RuntimeToolSpec] = {}
        self._governed_registry: Any | None = None
        self.usage_records: list[Any] = []
        for spec in specs or []:
            self.register(spec)

    @classmethod
    def default(cls) -> RuntimeToolRegistry:
        return cls(_default_tool_specs())

    @classmethod
    def from_tool_registry_v2(cls, governed_registry: Any) -> RuntimeToolRegistry:
        runtime = governed_registry.to_runtime_tool_registry()
        runtime._governed_registry = governed_registry
        return runtime

    def register(self, spec: RuntimeToolSpec) -> None:
        if spec.tool_name in self._specs:
            raise ValueError(f"Runtime tool already registered: {spec.tool_name}")
        if not spec.input_schema or spec.input_schema.get("type") != "object":
            raise ValueError(f"Runtime tool {spec.tool_name} must declare JSON input schema.")
        if not spec.output_schema or spec.output_schema.get("type") != "object":
            raise ValueError(f"Runtime tool {spec.tool_name} must declare JSON output schema.")
        if not spec.required_permissions:
            raise ValueError(f"Runtime tool {spec.tool_name} must declare permissions.")
        if spec.side_effect_level == "external_write" and not spec.requires_approval_by_default:
            raise ValueError(
                f"Runtime tool {spec.tool_name} performs external writes and requires approval."
            )
        _validate_tool_package_metadata(spec)
        if spec.category == "codex":
            _validate_codex_tool_boundaries(spec)
        self._specs[spec.tool_name] = spec

    def get(self, tool_name: str) -> RuntimeToolSpec | None:
        return self._specs.get(tool_name)

    def require(self, tool_name: str) -> RuntimeToolSpec:
        spec = self.get(tool_name)
        if spec is None:
            raise KeyError(f"Runtime tool is not registered: {tool_name}")
        return spec

    def list_tools(self) -> list[RuntimeToolSpec]:
        return list(self._specs.values())

    def tool_names(self) -> list[str]:
        return list(self._specs)

    def by_category(self, category: str) -> list[RuntimeToolSpec]:
        return [spec for spec in self._specs.values() if spec.category == category]

    def discover_approved_tools(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | list[str] | None = None,
        categories: set[str] | list[str] | None = None,
    ) -> list[RuntimeToolSpec]:
        if self._governed_registry is not None:
            tools = self._governed_registry.list_tools_visible_to_user(
                user_permissions=user_permissions,
                project_id=project_id,
                org_id=org_id,
            )
            category_filter = set(categories or [])
            if category_filter:
                tools = [tool for tool in tools if tool.category in category_filter]
            return tools
        permissions = set(user_permissions or [])
        category_filter = set(categories or [])
        discovered: list[RuntimeToolSpec] = []
        for spec in self._specs.values():
            if category_filter and spec.category not in category_filter:
                continue
            if not _tool_package_approved(spec):
                continue
            if not self.tool_allowed_in_context(
                spec,
                org_id=org_id,
                project_id=project_id,
                user_id=user_id,
                user_permissions=permissions,
            ):
                continue
            discovered.append(spec)
        return discovered

    def tool_allowed_in_context(
        self,
        spec: RuntimeToolSpec,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | list[str] | None = None,
    ) -> bool:
        policy = spec.metadata.get("tool_policy")
        if not isinstance(policy, dict):
            return True
        permissions = set(user_permissions or [])
        allowed_orgs = _string_set(policy.get("allowed_org_ids"))
        allowed_projects = _string_set(policy.get("allowed_project_ids"))
        allowed_users = _string_set(policy.get("allowed_user_ids"))
        denied_permissions = _string_set(policy.get("denied_permissions"))
        required_permissions = _string_set(policy.get("required_permissions"))
        if allowed_orgs and org_id not in allowed_orgs:
            return False
        if allowed_projects and project_id not in allowed_projects:
            return False
        if allowed_users and user_id not in allowed_users:
            return False
        if denied_permissions and permissions.intersection(denied_permissions):
            return False
        if required_permissions and not required_permissions.issubset(permissions):
            return False
        return True

    def track_usage(
        self,
        *,
        package_id: str,
        tool_name: str,
        tool_version: str,
        invoked_by: str,
        status: str,
        session_id: str | None = None,
        project_id: str | None = None,
        artifact_ids: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: Any | None = None,
        completed_at: Any | None = None,
    ) -> Any:
        if self._governed_registry is not None:
            usage = self._governed_registry.track_usage(
                package_id=package_id,
                tool_name=tool_name,
                tool_version=tool_version,
                invoked_by=invoked_by,
                status=status,
                session_id=session_id,
                project_id=project_id,
                artifact_ids=artifact_ids,
                warnings=warnings,
                metadata=metadata,
                started_at=started_at,
                completed_at=completed_at,
            )
            self.usage_records.append(usage)
            return usage
        from datetime import UTC, datetime

        from molecule_ranker.tool_ecosystem.schemas import ToolUsageRecord

        started = started_at or datetime.now(UTC)

        usage = ToolUsageRecord(
            usage_id=f"runtime-tool-usage-{len(self.usage_records) + 1}",
            session_id=session_id,
            project_id=project_id,
            package_id=package_id,
            tool_name=tool_name,
            tool_version=tool_version,
            invoked_by=invoked_by,  # type: ignore[arg-type]
            status=status,
            started_at=started,
            completed_at=completed_at,
            artifact_ids=artifact_ids or [],
            warnings=warnings or [],
            metadata=metadata or {},
        )
        self.usage_records.append(usage)
        return usage


def _validate_codex_tool_boundaries(spec: RuntimeToolSpec) -> None:
    blocked_tags = {"stage_gate", "campaign_advance"}
    if blocked_tags.intersection(spec.policy_tags):
        raise ValueError("Codex tools cannot approve stage gates or campaign advancement.")
    if "approve" in spec.tool_name or "advance" in spec.tool_name:
        raise ValueError("Codex tools cannot approve or advance campaigns.")
    if spec.side_effect_level != "codex_subprocess":
        raise ValueError("Codex tools must be declared as codex_subprocess side-effect tools.")


def _validate_tool_package_metadata(spec: RuntimeToolSpec) -> None:
    package = spec.metadata.get("tool_package")
    if package is None:
        return
    if not isinstance(package, dict):
        raise ValueError(f"Runtime tool {spec.tool_name} has invalid tool_package metadata.")
    if not _tool_package_approved(spec):
        raise ValueError(f"Runtime tool {spec.tool_name} is not from an approved tool package.")
    required = {
        "package_id",
        "version",
        "manifest_hash",
        "signature",
        "approval_status",
        "security_scan_status",
    }
    missing = sorted(key for key in required if not package.get(key))
    if missing:
        raise ValueError(
            f"Runtime tool {spec.tool_name} is missing tool package metadata: "
            + ", ".join(missing)
        )
    manifest_hash = str(package["manifest_hash"])
    signature = str(package["signature"])
    if manifest_hash.startswith("builtin:"):
        if signature != f"builtin-signature:{manifest_hash}":
            raise ValueError(f"Runtime tool {spec.tool_name} has invalid built-in signature.")
    elif signature != f"sha256:{manifest_hash}":
        raise ValueError(f"Runtime tool {spec.tool_name} has invalid package signature.")


def _tool_package_approved(spec: RuntimeToolSpec) -> bool:
    package = spec.metadata.get("tool_package")
    if package is None:
        return True
    if not isinstance(package, dict):
        return False
    return (
        package.get("approval_status") == "approved"
        and package.get("security_scan_status") == "passed"
        and package.get("status", "approved") == "approved"
    )


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _builtin_package_metadata() -> dict[str, str]:
    manifest_hash = f"builtin:molecule-ranker-runtime-tools:{__version__}"
    return {
        "package_id": "molecule-ranker-core-tools",
        "version": __version__,
        "manifest_hash": manifest_hash,
        "signature": f"builtin-signature:{manifest_hash}",
        "approval_id": "builtin-first-party-approval",
        "approval_status": "approved",
        "security_scan_id": "builtin-first-party-scan",
        "security_scan_status": "passed",
        "mcp_namespace": "molecule_ranker",
        "status": "approved",
    }


def _default_tool_specs() -> list[RuntimeToolSpec]:
    return [
        _spec(
            "create_project",
            "project",
            "Create a project workspace.",
            "db_write",
            "project:create",
            "molecule_ranker.workspace.store.ProjectWorkspaceStore",
        ),
        _spec(
            "list_projects",
            "project",
            "List visible project workspaces.",
            "none",
            "project:read",
            "molecule_ranker.platform.database.PlatformDatabase.list_projects",
        ),
        _spec(
            "show_project",
            "project",
            "Show a project workspace summary.",
            "none",
            "project:read",
            "molecule_ranker.workspace.store.ProjectWorkspaceStore.load",
        ),
        _spec(
            "register_artifacts",
            "project",
            "Register project artifacts.",
            "artifact_write",
            "artifact:write",
            "molecule_ranker.workspace.store.ProjectWorkspaceStore.register_artifact",
        ),
        _spec(
            "run_ranking",
            "ranking",
            "Run source-backed molecule ranking.",
            "artifact_write",
            "run:create",
            "molecule_ranker.cli.rank",
        ),
        _spec(
            "rerun_ranking",
            "ranking",
            "Rerun an existing ranking workflow.",
            "artifact_write",
            "run:create",
            "molecule_ranker.cli.rank",
        ),
        _spec(
            "summarize_ranking",
            "ranking",
            "Summarize ranking artifacts.",
            "none",
            "run:read",
            "molecule_ranker.agents.report_writer.ReportWriterAgent",
        ),
        _spec(
            "run_generation",
            "generation",
            "Run the generated molecule pipeline.",
            "artifact_write",
            "generation:run",
            "molecule_ranker.agents.novel_molecule.NovelMoleculeAgent",
        ),
        _spec(
            "run_design_loop",
            "generation",
            "Run AgentGraph design loop.",
            "artifact_write",
            "generation:run",
            "molecule_ranker.agents.scientific_design.ScientificDesignAgent",
        ),
        _spec(
            "benchmark_generation",
            "generation",
            "Benchmark molecule generation.",
            "artifact_write",
            "evaluation:run",
            "molecule_ranker.design.benchmarks",
        ),
        _spec(
            "run_developability",
            "developability",
            "Run developability assessment.",
            "artifact_write",
            "developability:run",
            "molecule_ranker.agents.developability_assessment.DevelopabilityAssessmentAgent",
        ),
        _spec(
            "assess_developability_artifact",
            "developability",
            "Assess a developability artifact.",
            "none",
            "developability:read",
            "molecule_ranker.developability.scoring",
        ),
        _spec(
            "run_literature_update",
            "literature",
            "Refresh source-backed literature evidence.",
            "external_read",
            "literature:update",
            "molecule_ranker.agents.literature_evidence.LiteratureEvidenceAgent",
        ),
        _spec(
            "summarize_literature",
            "literature",
            "Summarize literature artifacts.",
            "none",
            "literature:read",
            "molecule_ranker.evidence.normalizer",
        ),
        _spec(
            "import_assay_results",
            "experiments",
            "Import user-provided assay results.",
            "db_write",
            "experiment:write",
            "molecule_ranker.experiments.importers",
        ),
        _spec(
            "link_assay_results",
            "experiments",
            "Link assay results to candidates.",
            "db_write",
            "experiment:write",
            "molecule_ranker.experiments.linking",
        ),
        _spec(
            "summarize_assay_results",
            "experiments",
            "Summarize assay result artifacts.",
            "none",
            "experiment:read",
            "molecule_ranker.experiments.reports",
        ),
        _spec(
            "build_graph",
            "graph",
            "Build the cross-program knowledge graph.",
            "artifact_write",
            "graph:build",
            "molecule_ranker.knowledge_graph.builder",
        ),
        _spec(
            "query_graph",
            "graph",
            "Query graph artifacts.",
            "none",
            "graph:read",
            "molecule_ranker.knowledge_graph.queries",
        ),
        _spec(
            "detect_contradictions",
            "graph",
            "Detect graph contradictions.",
            "none",
            "graph:read",
            "molecule_ranker.knowledge_graph.contradictions",
        ),
        _spec(
            "detect_staleness",
            "graph",
            "Detect stale graph decisions.",
            "none",
            "graph:read",
            "molecule_ranker.knowledge_graph.staleness",
        ),
        _spec(
            "generate_hypotheses",
            "hypotheses",
            "Generate graph-backed hypotheses.",
            "artifact_write",
            "hypotheses:generate",
            "molecule_ranker.agents.hypothesis_generation.HypothesisGenerationAgent",
        ),
        _spec(
            "rank_hypotheses",
            "hypotheses",
            "Rank generated hypotheses.",
            "artifact_write",
            "hypotheses:rank",
            "molecule_ranker.hypotheses.ranking",
        ),
        _spec(
            "create_research_questions",
            "hypotheses",
            "Create research questions from hypotheses.",
            "artifact_write",
            "hypotheses:write",
            "molecule_ranker.hypotheses.research_questions",
        ),
        _spec(
            "build_portfolio_candidates",
            "portfolio",
            "Build portfolio candidate inputs.",
            "artifact_write",
            "portfolio:run",
            "molecule_ranker.portfolio.candidate_builder",
        ),
        _spec(
            "optimize_portfolio",
            "portfolio",
            "Run deterministic portfolio optimization.",
            "artifact_write",
            "portfolio:run",
            "molecule_ranker.portfolio.optimizer",
        ),
        _spec(
            "run_scenarios",
            "portfolio",
            "Run portfolio scenarios.",
            "artifact_write",
            "portfolio:run",
            "molecule_ranker.portfolio.scenarios",
        ),
        _spec(
            "create_campaign",
            "campaign",
            "Create a campaign record.",
            "db_write",
            "campaign:write",
            "molecule_ranker.campaigns.store",
        ),
        _spec(
            "plan_campaign",
            "campaign",
            "Plan a review-gated campaign.",
            "artifact_write",
            "campaign:plan",
            "molecule_ranker.campaigns.planner",
        ),
        _spec(
            "replan_campaign",
            "campaign",
            "Replan a campaign from deterministic triggers.",
            "artifact_write",
            "campaign:plan",
            "molecule_ranker.campaigns.replanning",
        ),
        _spec(
            "run_benchmark",
            "evaluation",
            "Run evaluation benchmark suite.",
            "artifact_write",
            "evaluation:run",
            "molecule_ranker.evaluation.benchmark_suite",
        ),
        _spec(
            "freeze_prospective_predictions",
            "evaluation",
            "Freeze prospective predictions.",
            "artifact_write",
            "evaluation:run",
            "molecule_ranker.evaluation.prospective",
        ),
        _spec(
            "run_guardrail_benchmark",
            "evaluation",
            "Run guardrail benchmark.",
            "artifact_write",
            "evaluation:run",
            "molecule_ranker.evaluation.guardrail_benchmark",
        ),
        _spec(
            "run_reproducibility_check",
            "evaluation",
            "Run reproducibility checks.",
            "artifact_write",
            "evaluation:run",
            "molecule_ranker.evaluation.reproducibility",
        ),
        _spec(
            "create_review_workspace",
            "review",
            "Create an expert review workspace.",
            "db_write",
            "review:write",
            "molecule_ranker.review.workspace",
        ),
        _spec(
            "create_dossier",
            "review",
            "Create a candidate dossier.",
            "artifact_write",
            "review:write",
            "molecule_ranker.review.dossier",
        ),
        _spec(
            "create_validation_handoff",
            "review",
            "Create validation handoff artifact.",
            "artifact_write",
            "review:write",
            "molecule_ranker.review.schemas.ValidationHandoff",
        ),
        _spec(
            "add_review_comment",
            "review",
            "Add a review comment.",
            "db_write",
            "review:write",
            "molecule_ranker.review.workspace.ProjectReviewWorkspace.add_comment",
        ),
        _spec(
            "request_followup",
            "review",
            "Request review follow-up.",
            "db_write",
            "review:write",
            "molecule_ranker.review.schemas.FollowupRequest",
        ),
        _spec(
            "health_check_integration",
            "integration",
            "Check integration health.",
            "external_read",
            "integration:read",
            "molecule_ranker.integrations.health",
        ),
        _spec(
            "dry_run_sync",
            "integration",
            "Run read-only integration sync dry run.",
            "external_read",
            "integration:read",
            "molecule_ranker.integrations.sync",
        ),
        _spec(
            "run_sync_write_enabled",
            "integration",
            "Run explicitly approved integration write sync.",
            "external_write",
            "integration:write",
            "molecule_ranker.integrations.sync",
            requires_approval=True,
            policy_tags=["external_write", "approval_required"],
        ),
        _spec(
            "run_readiness",
            "admin",
            "Run platform readiness checks.",
            "none",
            "admin:readiness",
            "molecule_ranker.platform.readiness",
        ),
        _spec(
            "generate_support_bundle",
            "admin",
            "Generate a redacted support bundle.",
            "artifact_write",
            "support:bundle",
            "molecule_ranker.pilot.support_bundle",
        ),
        _spec(
            "run_release_check",
            "admin",
            "Run release checks.",
            "none",
            "admin:release_check",
            "molecule_ranker.release.checks",
        ),
        _codex_spec(
            "summarize_artifacts",
            "Summarize registered artifacts.",
            "molecule_ranker.codex_backbone.provider.CodexBackboneProvider",
        ),
        _codex_spec(
            "explain_failure",
            "Explain a failed deterministic job.",
            "molecule_ranker.codex_backbone.provider.CodexBackboneProvider",
        ),
        _codex_spec(
            "plan_followup",
            "Draft follow-up planning output.",
            "molecule_ranker.codex_backbone.provider.CodexBackboneProvider",
        ),
        _codex_spec(
            "draft_memo",
            "Draft a reviewable memo.",
            "molecule_ranker.codex_backbone.provider.CodexBackboneProvider",
        ),
    ]


def _spec(
    tool_name: str,
    category: str,
    description: str,
    side_effect_level: str,
    permission: str,
    deterministic_entrypoint: str,
    *,
    requires_approval: bool = False,
    policy_tags: list[str] | None = None,
    idempotent: bool | None = None,
) -> RuntimeToolSpec:
    tags = policy_tags or []
    return RuntimeToolSpec(
        tool_name=tool_name,
        category=category,
        description=description,
        input_schema=dict(JSON_OBJECT_SCHEMA),
        output_schema=dict(JSON_OBJECT_SCHEMA),
        required_permissions=[permission],
        policy_tags=tags,
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval,
        idempotent=idempotent
        if idempotent is not None
        else side_effect_level in {"none", "external_read"},
        metadata={
            "deterministic_entrypoint": deterministic_entrypoint,
            "runtime_execution": RUNTIME_EXECUTION_MODE,
            "tool_package": _builtin_package_metadata(),
        },
    )


def _codex_spec(tool_name: str, description: str, deterministic_entrypoint: str) -> RuntimeToolSpec:
    return _spec(
        tool_name,
        "codex",
        description,
        "codex_subprocess",
        "codex:run",
        deterministic_entrypoint,
        policy_tags=["assistant_output_only", "no_biomedical_truth_claims"],
        idempotent=False,
    )


__all__ = ["RuntimeToolRegistry"]
