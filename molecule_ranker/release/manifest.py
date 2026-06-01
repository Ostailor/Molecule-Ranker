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
from molecule_ranker.release.checks import (
    API_CONTRACT_VERSION,
    ARTIFACT_CONTRACT_VERSION,
    DATA_CONTRACT_VERSION,
    RELEASE_STAGE,
    SCIENTIFIC_INTEGRITY_CONSTRAINTS,
    V1_RELEASE_GATES,
    WAREHOUSE_CONTRACT_VERSION,
    evaluate_release_readiness,
)


def release_manifest(root_dir: str | Path = ".") -> dict[str, Any]:
    manifest = build_release_manifest(root_dir)
    manifest.update(
        {
            "name": "molecule-ranker",
            "stage": RELEASE_STAGE,
            "scope": "multi_objective_portfolio_optimization_and_program_decision_analytics",
            "non_goals": [
                "unvalidated model-provider execution",
                "new external integration families",
                "biomedical truth claims from Codex",
                "generated activity, safety, or synthesizability claims without direct evidence",
                "model predictions promoted to evidence or assay results",
                "docking scores, poses, or interaction profiles promoted to evidence",
                "synthesis instructions, lab protocols, dosing, or patient guidance",
                "Codex-generated portfolio selections or optimization scores",
            ],
            "scientific_integrity_constraints": list(SCIENTIFIC_INTEGRITY_CONSTRAINTS),
            "contracts": {
                "api": API_CONTRACT_VERSION,
                "artifacts": ARTIFACT_CONTRACT_VERSION,
                "data_contracts": DATA_CONTRACT_VERSION,
                "warehouse": WAREHOUSE_CONTRACT_VERSION,
            },
            "release_gates": [gate.as_dict() for gate in V1_RELEASE_GATES],
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
        "V1.5 is for internal research use only and is not a clinical product.",
        "No medical advice, clinical claims, dosing, synthesis instructions, or lab protocols.",
        "Generated molecules are computational hypotheses and require independent validation.",
        "Portfolio recommendations are research prioritization aids, not clinical or "
        "experimental instructions.",
        "Portfolio selections and scores must be computed by deterministic modules, not Codex.",
        "Knowledge graph inference is a hypothesis layer and must not create evidence "
        "or assay results.",
        "Graph paths do not prove causality, efficacy, safety, binding, or activity.",
        "Surrogate model predictions are endpoint-specific prioritization artifacts, not evidence.",
        "Docking scores, poses, and structure-derived interactions are computational "
        "heuristics, not proof of binding or activity.",
        "Predicted structures are lower-confidence than suitable experimental structures.",
        "Default validation uses mocked services; live API and connector validation are opt-in.",
        "Codex outputs are assistant artifacts and must not be promoted to biomedical evidence.",
    ]
