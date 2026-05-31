from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .schemas import PortfolioCandidate, StageGate, StageGateDecision

STAGES = {
    "discovered",
    "generated",
    "computational_triage",
    "expert_review",
    "assay_candidate",
    "assay_tested",
    "followup_candidate",
    "deprioritized",
    "rejected",
    "candidate",
    "portfolio",
}

REVIEW_APPROVED_STATUSES = {
    "approved",
    "expert_approved",
    "reviewed",
    "triaged",
    "ready",
}


def build_stage_gate(
    candidate: PortfolioCandidate,
    *,
    from_stage: str,
    to_stage: str,
    require_human_approval: bool | None = None,
) -> StageGate:
    return evaluate_stage_gate(
        candidate,
        from_stage=from_stage,
        to_stage=to_stage,
        require_human_approval=require_human_approval,
    )


def evaluate_stage_gate(
    candidate: PortfolioCandidate,
    *,
    from_stage: str,
    to_stage: str,
    require_human_approval: bool | None = None,
    min_evidence_score: float = 0.35,
    min_structure_score: float = 0.35,
) -> StageGate:
    criteria = [
        _criterion(
            "minimum_evidence_score",
            _evidence_score(candidate) >= min_evidence_score
            or _has_direct_experimental_support(candidate),
            value=_evidence_score(candidate),
            threshold=min_evidence_score,
        ),
        _criterion(
            "review_approval",
            _review_approved(candidate),
            value=candidate.review_status,
            required=_requires_review_approval(candidate, to_stage),
        ),
        _criterion(
            "no_critical_developability_risk",
            not _has_critical_developability_risk(candidate),
            value=[*candidate.risk_flags, *candidate.blocking_risks],
        ),
        _criterion(
            "generated_molecule_direct_evidence_status",
            not candidate.generated_without_direct_evidence
            or to_stage not in {"assay_candidate", "assay_tested", "followup_candidate"},
            value={
                "origin": candidate.origin,
                "generated_without_direct_evidence": candidate.generated_without_direct_evidence,
                "direct_experimental_evidence": candidate.direct_experimental_evidence,
            },
        ),
        _criterion(
            "experiment_readiness_bucket",
            _readiness_bucket(candidate) in {"ready", "review_ready"}
            or to_stage not in {"assay_candidate", "followup_candidate"},
            value=_readiness_bucket(candidate),
        ),
        _criterion(
            "assay_result_status",
            not _failed_qc_only(candidate),
            value=_qc_statuses(candidate),
        ),
        _criterion(
            "model_prediction_calibration_status",
            _model_prediction_acceptable(candidate)
            or to_stage not in {"assay_candidate", "followup_candidate"},
            value=candidate.metadata.get("model_prediction_calibrated"),
        ),
        _criterion(
            "structure_assessment_quality",
            _structure_quality(candidate) >= min_structure_score
            or to_stage not in {"assay_candidate", "followup_candidate"},
            value=_structure_quality(candidate),
            threshold=min_structure_score,
        ),
        _criterion(
            "portfolio_selection_status",
            _portfolio_selected(candidate)
            or to_stage not in {"assay_candidate", "followup_candidate"},
            value=_portfolio_selection_status(candidate),
        ),
        _criterion(
            "codex_summary_not_sole_basis",
            not _codex_only_basis(candidate),
            value=candidate.metadata.get("codex_summary_only"),
        ),
        _criterion(
            "docking_score_not_sole_basis",
            not _docking_only_basis(candidate),
            value=candidate.metadata.get("docking_score"),
        ),
    ]
    required_approvals = _required_approvals(candidate, to_stage, require_human_approval)
    decision, rationale = _decision(criteria, required_approvals)
    return StageGate(
        stage_gate_id=f"stage-gate-{candidate.portfolio_candidate_id}-{from_stage}-to-{to_stage}",
        name=f"{candidate.candidate_name} {from_stage} to {to_stage}",
        from_stage=from_stage,
        to_stage=to_stage,
        criteria=criteria,
        required_approvals=required_approvals,
        decision=decision,
        rationale=rationale,
        metadata={
            "audit_event": True,
            "audit_event_type": "stage_gate_decision",
            "candidate_id": candidate.portfolio_candidate_id,
            "candidate_origin": candidate.origin,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "deterministic_validation": True,
            "codex_generated_decision": False,
            "known_stages": sorted(STAGES),
        },
    )


def _criterion(name: str, passed: bool, **values: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), **values}


def _decision(
    criteria: list[dict[str, Any]],
    required_approvals: list[str],
) -> tuple[StageGateDecision, str]:
    failed = [criterion for criterion in criteria if not criterion["passed"]]
    blocker_names = {criterion["name"] for criterion in failed}
    if "assay_result_status" in blocker_names:
        return "reject", "Failed-QC assay records block deterministic advancement."
    if "no_critical_developability_risk" in blocker_names:
        return "deprioritize", "Critical developability risk requires deprioritization or review."
    if "codex_summary_not_sole_basis" in blocker_names:
        return "hold", "Codex summary alone cannot advance a candidate."
    if "docking_score_not_sole_basis" in blocker_names:
        return "hold", "Docking score alone cannot advance a candidate."
    if failed:
        return "needs_more_data", "One or more deterministic gate criteria require more data."
    if required_approvals:
        return "needs_more_data", "Human approval is required for this stage gate."
    return "advance", "All deterministic stage-gate criteria passed."


def _required_approvals(
    candidate: PortfolioCandidate,
    to_stage: str,
    require_human_approval: bool | None,
) -> list[str]:
    approvals: set[str] = set()
    if require_human_approval is True:
        approvals.add("program_lead")
    if _requires_review_approval(candidate, to_stage):
        approvals.add("expert_reviewer")
    if candidate.risk_flags or candidate.blocking_risks:
        approvals.add("program_lead")
    return sorted(approvals)


def _requires_review_approval(candidate: PortfolioCandidate, to_stage: str) -> bool:
    return candidate.origin == "generated" and to_stage in {
        "assay_candidate",
        "assay_tested",
        "followup_candidate",
    }


def _review_approved(candidate: PortfolioCandidate) -> bool:
    if candidate.review_status is None:
        return False
    normalized = candidate.review_status.lower().replace("-", "_").replace(" ", "_")
    return normalized in REVIEW_APPROVED_STATUSES


def _evidence_score(candidate: PortfolioCandidate) -> float:
    values = [
        value
        for value in (candidate.evidence_score, candidate.experimental_support_score)
        if isinstance(value, int | float)
    ]
    return max(values) if values else 0.0


def _has_direct_experimental_support(candidate: PortfolioCandidate) -> bool:
    return candidate.direct_experimental_evidence and not _failed_qc_only(candidate)


def _has_critical_developability_risk(candidate: PortfolioCandidate) -> bool:
    text = " ".join([*candidate.risk_flags, *candidate.blocking_risks]).lower()
    return "critical" in text and ("developability" in text or "rejected" in text)


def _readiness_bucket(candidate: PortfolioCandidate) -> str:
    explicit = candidate.metadata.get("experiment_readiness_bucket")
    if explicit:
        return str(explicit)
    score = candidate.experiment_readiness_score
    if score is None:
        return "missing"
    if score >= 0.75:
        return "ready"
    if score >= 0.5:
        return "review_ready"
    return "not_ready"


def _qc_statuses(candidate: PortfolioCandidate) -> list[str]:
    statuses: list[str] = []
    for key in ("qc_statuses", "qc_status", "experimental_qc_statuses"):
        raw = candidate.metadata.get(key)
        if isinstance(raw, list):
            statuses.extend(str(value).lower() for value in raw if value)
        elif raw:
            statuses.append(str(raw).lower())
    for record in candidate.metadata.get("experimental_evidence_records", []):
        if isinstance(record, dict) and record.get("qc_status"):
            statuses.append(str(record["qc_status"]).lower())
    return statuses


def _failed_qc_only(candidate: PortfolioCandidate) -> bool:
    statuses = _qc_statuses(candidate)
    return bool(statuses) and all(status in {"failed", "fail", "rejected"} for status in statuses)


def _model_prediction_acceptable(candidate: PortfolioCandidate) -> bool:
    if candidate.predictive_model_score is None:
        return True
    if candidate.metadata.get("model_out_of_domain") is True:
        return False
    return candidate.metadata.get("model_prediction_calibrated") is True


def _structure_quality(candidate: PortfolioCandidate) -> float:
    value = candidate.structure_score
    if isinstance(value, int | float):
        return min(1.0, max(0.0, float(value)))
    metadata_value = candidate.metadata.get("structure_confidence")
    if isinstance(metadata_value, int | float):
        return min(1.0, max(0.0, float(metadata_value)))
    return 0.0


def _portfolio_selected(candidate: PortfolioCandidate) -> bool:
    status = _portfolio_selection_status(candidate)
    return status in {"selected", "recommended", "approved"}


def _portfolio_selection_status(candidate: PortfolioCandidate) -> str | None:
    value = candidate.metadata.get("portfolio_selection_status")
    return str(value).lower() if value else None


def _codex_only_basis(candidate: PortfolioCandidate) -> bool:
    return bool(candidate.metadata.get("codex_summary_only")) and not (
        candidate.evidence_score is not None
        or candidate.experimental_support_score is not None
        or candidate.predictive_model_score is not None
        or candidate.structure_score is not None
        or candidate.direct_experimental_evidence
    )


def _docking_only_basis(candidate: PortfolioCandidate) -> bool:
    return bool(candidate.metadata.get("docking_score_only")) or (
        candidate.structure_score is not None
        and candidate.evidence_score is None
        and candidate.experimental_support_score is None
        and candidate.predictive_model_score is None
        and not candidate.direct_experimental_evidence
    )
