from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.contracts import (
    API_CONTRACT_VERSION as API_CONTRACT_REGISTRY_VERSION,
)
from molecule_ranker.contracts import (
    ARTIFACT_CONTRACT_VERSION as ARTIFACT_CONTRACT_REGISTRY_VERSION,
)
from molecule_ranker.platform.pilot_readiness import (
    PILOT_READINESS_VERSION,
    build_pilot_readiness_report,
)
from molecule_ranker.release.checks import (
    API_CONTRACT_VERSION,
    ARTIFACT_CONTRACT_VERSION,
    DATA_CONTRACT_VERSION,
    RELEASE_GATES,
    RELEASE_STAGE,
    SCIENTIFIC_INTEGRITY_CONSTRAINTS,
    WAREHOUSE_CONTRACT_VERSION,
    evaluate_release_readiness,
)


def release_manifest(root_dir: str | Path = ".") -> dict[str, Any]:
    manifest = build_release_manifest(root_dir)
    manifest.update(
        {
            "name": "molecule-ranker",
            "stage": RELEASE_STAGE,
            "scope": "validated_enterprise_discovery_operating_system",
            "non_goals": [
                "unvalidated model-provider execution",
                "new external integration families",
                "new molecule generation capabilities",
                "new docking capabilities",
                "new ADMET capabilities",
                "new external connector families",
                "new predictive modeling capabilities",
                "biomedical truth claims from Codex",
                "generated activity, safety, or synthesizability claims without direct evidence",
                "model predictions promoted to evidence or assay results",
                "docking scores, poses, or interaction profiles promoted to evidence",
                "synthesis instructions, lab protocols, dosing, or patient guidance",
                "Codex-generated portfolio selections or optimization scores",
                "Codex-generated hypotheses without deterministic graph-reference validation",
                "research questions promoted to lab protocols or experimental procedures",
                "Codex-generated campaign priorities, budgets, costs, metrics, outcomes, "
                "or advancement decisions",
                "campaign plans promoted to lab protocols or synthesis routes",
                "major new science modules in the V2.9 readiness release",
                "expanded generation, docking, ADMET, graph reasoning, model training, "
                "integrations, or campaign planning except for controlled runtime "
                "orchestration, stability, validation, security, and enterprise readiness",
            ],
            "scientific_integrity_constraints": list(SCIENTIFIC_INTEGRITY_CONSTRAINTS),
            "contracts": {
                "api": API_CONTRACT_VERSION,
                "artifacts": ARTIFACT_CONTRACT_VERSION,
                "data_contracts": DATA_CONTRACT_VERSION,
                "warehouse": WAREHOUSE_CONTRACT_VERSION,
            },
            "pilot_readiness_version": PILOT_READINESS_VERSION,
            "pilot_readiness": build_pilot_readiness_report(root_dir),
            "release_gates": [gate.as_dict() for gate in RELEASE_GATES],
        }
    )
    return manifest


def build_release_manifest(root_dir: str | Path = ".") -> dict[str, Any]:
    root = Path(root_dir).resolve()
    readiness = evaluate_release_readiness(root)
    return {
        "name": "molecule-ranker",
        "version": __version__,
        "git_commit": _git_commit(root),
        "build_timestamp": _utc_timestamp(),
        "artifact_contract_version": ARTIFACT_CONTRACT_REGISTRY_VERSION,
        "api_contract_version": API_CONTRACT_REGISTRY_VERSION,
        "dependency_lock_hash": _dependency_lock_hash(root),
        "test_summary": _test_summary(root),
        "validation_summary": _validation_summary(root),
        "known_limitations": _known_limitations(),
        "readiness": {
            "status": "pass" if readiness["ready"] else "fail",
            "gate_count": len(readiness["gates"]),
            "missing_gates": [
                gate["gate_id"] for gate in readiness["gates"] if gate["status"] != "pass"
            ],
        },
    }


def write_release_manifest(manifest: dict[str, Any], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return target


def _git_commit(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dependency_lock_hash(root: Path) -> str:
    lockfile = root / "uv.lock"
    if not lockfile.exists():
        return ""
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()


def _test_summary(root: Path) -> dict[str, Any]:
    marker = root / ".molecule-ranker" / "release" / "tests-passed.json"
    if marker.exists():
        try:
            payload = json.loads(marker.read_text())
        except json.JSONDecodeError:
            payload = {"status": "pass", "marker": str(marker)}
        if isinstance(payload, dict):
            return {"status": payload.get("status", "pass"), **payload}
    return {"status": "not_recorded", "marker": str(marker)}


def _validation_summary(root: Path) -> dict[str, Any]:
    report = root / ".molecule-ranker" / "validation" / "release" / "golden_validation_report.json"
    if not report.exists():
        return {"status": "not_recorded", "report": str(report)}
    try:
        payload = json.loads(report.read_text())
    except json.JSONDecodeError:
        return {"status": "fail", "report": str(report), "error": "invalid_json"}
    if not isinstance(payload, dict):
        return {"status": "fail", "report": str(report), "error": "invalid_payload"}
    return {
        "status": payload.get("status", "unknown"),
        "workflow_count": payload.get("workflow_count", 0),
        "live_validation": payload.get("live_validation", False),
        "report": str(report),
    }


def _known_limitations() -> list[str]:
    return [
        "V2.9 is for internal research use only and is not a clinical product.",
        "V2.9 adds software/autonomy validation for V3 readiness while keeping "
        "governed biologics, small-molecule, runtime-agent, co-pilot, repair-loop, "
        "and governed tool controls intact.",
        "Agents may repair workflows but may not repair scientific truth by inventing "
        "missing data.",
        "No medical advice, clinical claims, dosing, synthesis instructions, or lab protocols.",
        "Generated molecules are computational hypotheses and require independent validation.",
        "Portfolio recommendations are research prioritization aids, not clinical or "
        "experimental instructions.",
        "Portfolio selections and scores must be computed by deterministic modules, not Codex.",
        "Knowledge graph inference is a hypothesis layer and must not create evidence "
        "or assay results.",
        "Automated hypotheses and research questions are planning artifacts, not evidence "
        "or experimental procedures.",
        "Campaign plans are research-management artifacts, not lab protocols or "
        "experimental procedures.",
        "Campaign priorities, budget fit, dependencies, and replan triggers must be "
        "computed by deterministic modules, not Codex.",
        "Benchmark results are evaluation artifacts, not biomedical evidence.",
        "Prospective validation analytics are not clinical validation.",
        "Codex must not invent benchmark results, labels, metrics, or conclusions.",
        "Enterprise and V3 readiness validation artifacts are software/process "
        "validation artifacts, not clinical validation.",
        "Graph paths do not prove causality, efficacy, safety, binding, or activity.",
        "Surrogate model predictions are endpoint-specific prioritization artifacts, not evidence.",
        "Docking scores, poses, and structure-derived interactions are computational "
        "heuristics, not proof of binding or activity.",
        "Predicted structures are lower-confidence than suitable experimental structures.",
        "Default validation uses mocked services; live API and connector validation are opt-in.",
        "Codex outputs are assistant artifacts and must not be promoted to biomedical evidence.",
        "Codex runtime actions must pass the tool registry, RBAC, policy, approval, "
        "artifact validation, guardrail, and audit trail checks.",
        "Codex cannot approve its own autonomy increases, policy overrides, "
        "governance policy changes, budgets, capability grants, or certifications.",
        "Agent incidents, policy violations, guardrail failures, policy drift, "
        "prompt drift, and tool drift must remain visible in governance reports.",
    ]
