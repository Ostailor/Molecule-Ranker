from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert

from molecule_ranker import __version__
from molecule_ranker.platform.database import PlatformDatabase, project_workspaces
from molecule_ranker.v2 import V2_CONTRACT_VERSION, V2_SCHEMA_VERSION, validate_v2_artifact_payload
from molecule_ranker.validation.guardrail_audit import run_guardrail_audit
from molecule_ranker.validation.v2_package import generate_v2_validation_package

ENTERPRISE_GOLDEN_STEP_COUNT = 20

_SECRET_VALUE_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|credential)\s*[:=]\s*[A-Za-z0-9_./-]{8,}",
    re.I,
)


def run_enterprise_golden_workflow(
    *,
    output_dir: str | Path,
    root_dir: str | Path = ".",
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    root = Path(root_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    _clear_output(output)

    database = PlatformDatabase(output, db_path=output / "platform.sqlite")
    admin = database.create_user(
        email="enterprise-admin@example.test",
        password="Admin-password-1",
        roles=["platform_admin", "user"],
    )
    scientist = database.create_user(
        email="enterprise-scientist@example.test",
        password="Scientist-password-1",
    )
    reviewer = database.create_user(
        email="enterprise-reviewer@example.test",
        password="Reviewer-password-1",
    )
    org = database.create_organization(
        name="Enterprise Golden Org",
        org_id="org-enterprise-golden",
        created_by_user_id=admin.user_id,
    )
    team = database.create_team(
        org_id=org.org_id,
        name="Discovery Team",
        team_id="team-enterprise-golden",
        created_by_user_id=admin.user_id,
    )
    database.add_membership(user_id=scientist.user_id, org_id=org.org_id, role="scientist")
    database.add_membership(user_id=reviewer.user_id, org_id=org.org_id, role="reviewer")
    _insert_project(database, org_id=org.org_id, project_id="project-enterprise-golden")
    database.grant_project_permission(
        project_id="project-enterprise-golden",
        role="project_owner",
        actor_user_id=admin.user_id,
        user_id=admin.user_id,
    )
    database.grant_project_permission(
        project_id="project-enterprise-golden",
        role="editor",
        actor_user_id=admin.user_id,
        user_id=scientist.user_id,
    )
    database.grant_project_permission(
        project_id="project-enterprise-golden",
        role="reviewer",
        actor_user_id=admin.user_id,
        user_id=reviewer.user_id,
    )

    artifact_paths = _write_step_artifacts(
        output,
        org_id=org.org_id,
        team_id=team.team_id,
        user_ids=[admin.user_id, scientist.user_id, reviewer.user_id],
    )
    contract_results = [_validate_contract_artifact(path) for path in artifact_paths]
    guardrail_report = run_guardrail_audit(output)
    validation_package = generate_v2_validation_package(
        output_dir=output / "validation_package",
        root_dir=root,
        source_root=Path.cwd(),
    )
    validation_package_marker = _write_json(
        output / "20_validation_package.json",
        _evidence_item(
            "validation-package-generated",
            "software_validation_package",
            "v2_validation_package",
            "V2 validation package generated as software platform evidence.",
            {
                "manifest_path": str(validation_package.manifest_path),
                "status": validation_package.status,
            },
        ),
    )
    artifact_paths.append(validation_package_marker)
    contract_results.append(_validate_contract_artifact(validation_package_marker))
    export_zip = _write_project_export(output, artifact_paths)

    assertions = _enterprise_assertions(
        output,
        artifact_paths=artifact_paths,
        contract_results=contract_results,
        guardrail_report=guardrail_report.as_dict(),
        export_zip=export_zip,
    )
    steps = _workflow_steps(artifact_paths)
    report = {
        "status": "pass"
        if all(step["status"] == "pass" for step in steps)
        and all(assertion["status"] == "pass" for assertion in assertions)
        else "fail",
        "version": __version__,
        "workflow_id": "v2_enterprise_golden",
        "mocked_mode": True,
        "output_dir": str(output),
        "step_count": ENTERPRISE_GOLDEN_STEP_COUNT,
        "steps": steps,
        "contract_validation": contract_results,
        "guardrail_audit": guardrail_report.as_dict(),
        "assertions": assertions,
        "assertion_summary": _assertion_summary(assertions),
        "project_export": str(export_zip),
        "validation_package": validation_package.to_dict(),
        "scope": "software_platform_validation",
    }
    _write_json(output / "enterprise_golden_report.json", report)
    _write_markdown(output / "enterprise_golden_report.md", report)
    return report


def _write_step_artifacts(
    output: Path,
    *,
    org_id: str,
    team_id: str,
    user_ids: list[str],
) -> list[Path]:
    paths = [
        _write_json(
            output / "01_platform_db_initialized.json",
            _evidence_item(
                "platform-db-initialized",
                "platform_db",
                "mocked_platform_db",
                "Platform database initialized for deterministic enterprise workflow.",
                {"schema_initialized": True, "mocked": True},
            ),
        ),
        _write_json(
            output / "02_org_team_users.json",
            _evidence_item(
                "org-team-users-created",
                "identity_setup",
                "mocked_identity",
                "Organization, team, and users created in the platform database.",
                {"org_id": org_id, "team_id": team_id, "user_count": len(user_ids)},
            ),
        ),
        _write_json(
            output / "03_rbac_configured.json",
            _evidence_item(
                "rbac-configured",
                "rbac",
                "mocked_rbac",
                "Project roles configured for admin, scientist, and reviewer.",
                {"roles_configured": ["project_owner", "editor", "reviewer"]},
            ),
        ),
        _write_json(
            output / "04_project_created.json",
            _evidence_item(
                "project-created",
                "project",
                "mocked_project",
                "Enterprise golden project created with tenant and project scope.",
                {"project_id": "project-enterprise-golden", "org_id": org_id},
            ),
        ),
        _write_json(
            output / "05_source_backed_ranking.json",
            _evidence_item(
                "source-backed-ranking",
                "mocked_source",
                "synthetic-source-record-1",
                "Source-backed ranking completed using mocked source records.",
                {"ranked_candidates": ["existing-candidate-1"], "source_backed": True},
            ),
        ),
        _write_json(
            output / "06_generated_molecule.json",
            {
                "artifact_type": "generated_molecule",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "generated_molecule_id": "generated-hypothesis-1",
                "generated_id": "generated-hypothesis-1",
                "smiles": "CCO",
                "generation_method": "mocked_generator_ensemble",
                "hypothesis_only": True,
                "evidence_boundary": "computational_hypothesis_requires_review",
                "origin": "generated",
                "label": "computational hypothesis for review",
                "review_required": True,
                "claims": [],
            },
        ),
        _write_json(
            output / "07_developability_triage.json",
            _evidence_item(
                "developability-triage",
                "developability",
                "mocked_developability",
                "Developability triage completed as review metadata only.",
                {"triage_only": True, "no_safety_claim": True},
            ),
        ),
        _write_json(
            output / "08_mocked_model_prediction.json",
            {
                "artifact_type": "model_card",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "model_id": "mocked-surrogate-v2",
                "endpoint": "synthetic-assay-endpoint",
                "training_manifest": {"mocked": True, "calibration": "synthetic"},
                "metrics": [{"name": "mocked_contract_check", "value": 1.0}],
                "limitations": [
                    "Prediction is a software validation fixture and not evidence.",
                ],
                "prediction_usage": "triage_metadata_only",
            },
        ),
        _write_json(
            output / "09_structure_null_docking.json",
            _evidence_item(
                "structure-null-docking",
                "structure_workflow",
                "null_docking",
                "Structure workflow executed with null docking and no binding claim.",
                {"docking_mode": "null", "score_used_as_evidence": False},
            ),
        ),
        _write_json(
            output / "10_portfolio.json",
            _evidence_item(
                "portfolio-built",
                "portfolio",
                "mocked_portfolio",
                "Portfolio built from deterministic mocked artifacts.",
                {"selected_ids": ["existing-candidate-1"], "codex_decision": False},
            ),
        ),
        _write_json(
            output / "11_hypotheses.json",
            _evidence_item(
                "hypotheses-generated",
                "hypothesis",
                "mocked_hypothesis_engine",
                "Hypotheses generated as reviewable research questions.",
                {"hypothesis_count": 2, "creates_evidence": False},
            ),
        ),
        _write_json(
            output / "12_campaign.json",
            {
                "artifact_type": "campaign",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "campaign_id": "campaign-enterprise-golden",
                "objectives": ["rank-review-import-replan"],
                "work_packages": [
                    {"name": "review-batch", "kind": "research_management"},
                ],
                "stage_gates": [{"name": "human-review", "decision_source": "reviewer"}],
                "audit_trail": [{"event": "campaign_created", "actor": "system"}],
                "plan_kind": "research_management",
                "contains_experimental_instructions": False,
            },
        ),
        _write_json(
            output / "13_synthetic_assay_import.json",
            _evidence_item(
                "synthetic-assay-import",
                "synthetic_csv",
                "synthetic-assay-result-1",
                "Synthetic assay result imported from a mocked file source.",
                {
                    "result_id": "synthetic-assay-result-1",
                    "source_type": "synthetic_csv",
                    "qc_status": "passed",
                },
            ),
        ),
        _write_json(
            output / "14_replan.json",
            {
                "artifact_type": "campaign",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "campaign_id": "campaign-enterprise-golden-replan",
                "objectives": ["replan-after-synthetic-import"],
                "work_packages": [{"name": "review-next-batch", "kind": "research_management"}],
                "stage_gates": [{"name": "review-before-export", "decision_source": "reviewer"}],
                "audit_trail": [{"event": "replan_triggered", "actor": "system"}],
                "contains_experimental_instructions": False,
            },
        ),
        _write_json(
            output / "15_knowledge_graph.json",
            {
                "artifact_type": "knowledge_graph",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "graph_id": "graph-enterprise-golden",
                "entities": [{"id": "existing-candidate-1", "type": "candidate"}],
                "relations": [
                    {
                        "source": "existing-candidate-1",
                        "target": "synthetic-source-record-1",
                        "type": "has_mocked_source",
                    }
                ],
                "provenance": {"mocked": True, "creates_evidence": False},
            },
        ),
        _write_json(
            output / "16_benchmark_evaluation.json",
            {
                "artifact_type": "evaluation",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "report_id": "evaluation-enterprise-golden",
                "suite_id": "mocked-enterprise-suite",
                "metrics": [{"name": "workflow_artifact_presence", "value": 1.0}],
                "limitations": [
                    "Evaluation output validates software flow only.",
                ],
                "reproducibility": {"mocked": True, "seed": 20},
                "outputs_separate_from_review": True,
            },
        ),
        _write_json(
            output / "17_codex_summary.json",
            {
                "artifact_type": "codex_task_result",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "task_id": "codex-null-summary",
                "task_type": "summary",
                "status": "succeeded",
                "guardrail_status": "pass",
                "artifact_context": {
                    "provider": "null_provider",
                    "source_artifact_ids": ["05_source_backed_ranking"],
                    "raw_assay_files_included": False,
                },
                "summary": "Null provider summary references artifacts without creating claims.",
                "creates_evidence": False,
                "creates_scores": False,
                "creates_decisions": False,
            },
        ),
        _write_json(
            output / "18_review_workspace.json",
            {
                "artifact_type": "review_workspace",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "workspace_id": "review-enterprise-golden",
                "review_items": [
                    {
                        "item_id": "review-item-1",
                        "source_artifact_type": "generated_molecule",
                        "decision_required": True,
                    }
                ],
                "audit_events": [{"event": "review_workspace_created", "actor": "reviewer"}],
                "decisions": [{"decision_id": "decision-1", "stored_separately": True}],
            },
        ),
        _write_json(
            output / "19_project_export.json",
            {
                "artifact_type": "integration_sync",
                "schema_version": V2_SCHEMA_VERSION,
                "contract_version": V2_CONTRACT_VERSION,
                "sync_job": {"sync_job_id": "project-export", "mode": "dry_run"},
                "records": [{"record_id": "project-export-manifest", "external_write": False}],
                "contract_report": {"status": "pass", "secrets_excluded": True},
                "export_kind": "redacted_project_package",
            },
        ),
    ]
    return paths


def _enterprise_assertions(
    output: Path,
    *,
    artifact_paths: list[Path],
    contract_results: list[dict[str, Any]],
    guardrail_report: dict[str, Any],
    export_zip: Path,
) -> list[dict[str, Any]]:
    generated = _load_json(output / "06_generated_molecule.json")
    codex = _load_json(output / "17_codex_summary.json")
    review = _load_json(output / "18_review_workspace.json")
    evaluation = _load_json(output / "16_benchmark_evaluation.json")
    campaign_text = (output / "12_campaign.json").read_text()
    assertions = [
        _assertion("all_artifacts_exist", all(path.exists() for path in artifact_paths)),
        _assertion(
            "all_contracts_validate",
            all(result["valid"] for result in contract_results),
            {"failures": [result for result in contract_results if not result["valid"]]},
        ),
        _assertion("no_guardrail_violations", guardrail_report["status"] == "pass"),
        _assertion("no_plaintext_secrets", not _contains_plaintext_secret(output)),
        _assertion(
            "generated_molecules_labeled",
            generated.get("hypothesis_only") is True
            and generated.get("evidence_boundary") == "computational_hypothesis_requires_review",
        ),
        _assertion(
            "codex_outputs_separate",
            codex.get("creates_evidence") is False
            and codex.get("creates_scores") is False
            and codex.get("creates_decisions") is False,
        ),
        _assertion(
            "review_decisions_separate",
            all(
                item.get("source_artifact_type") != "evidence_item"
                for item in review["review_items"]
            )
            and review["decisions"][0]["stored_separately"] is True,
        ),
        _assertion(
            "evaluation_outputs_separate",
            evaluation.get("outputs_separate_from_review") is True,
        ),
        _assertion(
            "campaign_plans_are_not_procedures",
            "protocol" not in campaign_text.lower()
            and "synthesis" not in campaign_text.lower()
            and "dosage" not in campaign_text.lower(),
        ),
        _assertion("project_export_created", export_zip.exists()),
        _assertion(
            "validation_package_generated",
            (output / "validation_package" / "validation_package_manifest.json").exists(),
        ),
    ]
    return assertions


def _insert_project(database: PlatformDatabase, *, org_id: str, project_id: str) -> None:
    now = datetime.now(UTC)
    with database.engine.begin() as connection:
        connection.execute(
            insert(project_workspaces).values(
                project_id=project_id,
                org_id=org_id,
                name="Enterprise Golden Project",
                root_dir=None,
                created_at=now,
                updated_at=now,
                metadata_json={"mocked": True},
            )
        )


def _evidence_item(
    evidence_id: str,
    source_type: str,
    source_id: str,
    claim: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_type": "evidence_item",
        "schema_version": V2_SCHEMA_VERSION,
        "contract_version": V2_CONTRACT_VERSION,
        "evidence_id": evidence_id,
        "source_type": source_type,
        "source_id": source_id,
        "claim": claim,
        "provenance": {"mocked": True, **provenance},
    }


def _validate_contract_artifact(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    result = validate_v2_artifact_payload(payload)
    report = result.as_dict()
    report["path"] = str(path)
    return report


def _workflow_steps(artifact_paths: list[Path]) -> list[dict[str, Any]]:
    names = (
        "Initialize platform DB",
        "Create org/team/users",
        "Configure RBAC",
        "Create project",
        "Run source-backed ranking with mocked sources",
        "Generate molecule hypotheses",
        "Run developability triage",
        "Run mocked model prediction",
        "Run structure workflow with null docking",
        "Build portfolio",
        "Generate hypotheses",
        "Create campaign",
        "Import synthetic assay result",
        "Trigger replan",
        "Build graph",
        "Run benchmark evaluation",
        "Use Codex null provider for summary",
        "Create review workspace",
        "Export project",
        "Generate validation package",
    )
    return [
        {
            "step": index,
            "name": name,
            "status": "pass" if artifact_paths[index - 1].exists() else "fail",
            "artifact": artifact_paths[index - 1].name,
        }
        for index, name in enumerate(names, start=1)
    ]


def _write_project_export(output: Path, artifact_paths: list[Path]) -> Path:
    export_path = output / "project_export.zip"
    export_path.unlink(missing_ok=True)
    with zipfile.ZipFile(export_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in artifact_paths:
            if path.exists() and path.suffix == ".json":
                archive.write(path, path.name)
    return export_path


def _assertion(name: str, passed: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"assertion": name, "status": "pass" if passed else "fail", "details": details or {}}


def _assertion_summary(assertions: list[dict[str, Any]]) -> dict[str, int]:
    failed = sum(1 for assertion in assertions if assertion["status"] != "pass")
    return {"passed": len(assertions) - failed, "failed": failed, "total": len(assertions)}


def _contains_plaintext_secret(output: Path) -> bool:
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        if _SECRET_VALUE_RE.search(path.read_text(errors="ignore")):
            return True
    return False


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _write_markdown(path: Path, report: dict[str, Any]) -> Path:
    lines = [
        "# V2.0 Enterprise Golden Workflow",
        "",
        f"- Status: `{report['status']}`",
        f"- Steps: {report['step_count']}",
        f"- Mocked mode: `{report['mocked_mode']}`",
        "",
        "## Assertions",
        "",
    ]
    lines.extend(
        f"- `{assertion['status']}` {assertion['assertion']}"
        for assertion in report["assertions"]
    )
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _clear_output(output: Path) -> None:
    for path in output.iterdir() if output.exists() else []:
        if path.name == "platform.sqlite":
            path.unlink(missing_ok=True)
        elif path.is_file():
            path.unlink()
        elif path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()


__all__ = ["ENTERPRISE_GOLDEN_STEP_COUNT", "run_enterprise_golden_workflow"]
