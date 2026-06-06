from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.review.audit import audit_event
from molecule_ranker.review.feedback import FeedbackStore, apply_feedback_to_review_item
from molecule_ranker.review.schemas import PriorityBucket, Reviewer, ReviewItem, ReviewWorkspace
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    GeneratedMoleculeHypothesis,
    LiteratureEvidenceBundle,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
)
from molecule_ranker.utils import slugify


def build_review_workspace(
    ranking_run: RankingRun,
    *,
    config: dict[str, Any] | None = None,
    report_artifacts: dict[str, str] | None = None,
    reviewer: Reviewer | None = None,
) -> ReviewWorkspace:
    """Build a V0.5 review workspace from a completed ranking run."""
    config = config or {}
    run_id = str(config.get("run_id") or _run_id_from_ranking_run(ranking_run))
    disease_name = ranking_run.disease.canonical_name
    target_evidence_counts = {
        target.symbol: len(target.evidence) for target in ranking_run.targets
    }
    items = [
        _item_from_candidate(
            candidate,
            run_id=run_id,
            disease_name=disease_name,
            target_evidence_counts=target_evidence_counts,
            config=config,
        )
        for candidate in ranking_run.candidates
    ]
    items.extend(
        _item_from_generated(
            candidate,
            run_id=run_id,
            disease_name=disease_name,
            target_evidence_counts=target_evidence_counts,
            config=config,
        )
        for candidate in ranking_run.generated_candidates
    )
    items.extend(_items_from_biologics_config(config, run_id, disease_name))
    items = _apply_feedback_prior(
        items,
        config=config,
        disease_name=disease_name,
    )
    actor = reviewer.reviewer_id if reviewer is not None else "local-reviewer"
    workspace = ReviewWorkspace(
        run_id=run_id,
        disease_name=disease_name,
        created_at=datetime.now(UTC),
        review_items=items,
        metadata={
            "source": "ranking_run",
            "report_artifacts": report_artifacts or {},
            "config": config,
        },
    )
    workspace.audit_events.append(
        audit_event(
            event_type="workspace_created",
            actor=actor,
            object_type="ReviewWorkspace",
            object_id=workspace.workspace_id,
            summary=f"Created review workspace with {len(items)} items.",
            after={"item_count": len(items)},
        )
    )
    return workspace


def build_review_workspace_from_artifact(
    payload: dict[str, Any],
    *,
    reviewer: Reviewer | None = None,
    run_id: str | None = None,
) -> ReviewWorkspace:
    disease_payload = payload.get("disease")
    disease = disease_payload if isinstance(disease_payload, dict) else {}
    disease_name = str(disease.get("canonical_name") or payload.get("disease_name") or "unknown")
    resolved_run_id = run_id or str(payload.get("run_id") or f"run-{slugify(disease_name)}")
    items = [
        *_items_from_existing(payload, resolved_run_id, disease_name),
        *_items_from_generated(payload, resolved_run_id, disease_name),
        *_items_from_biologics_payload(payload, resolved_run_id, disease_name),
    ]
    actor = reviewer.reviewer_id if reviewer is not None else "local-reviewer"
    workspace = ReviewWorkspace(
        run_id=resolved_run_id,
        disease_name=disease_name,
        created_at=datetime.now(UTC),
        review_items=items,
        metadata={"source": "artifact"},
    )
    workspace.audit_events.append(
        audit_event(
            event_type="workspace_created",
            actor=actor,
            object_type="ReviewWorkspace",
            object_id=workspace.workspace_id,
            summary=f"Created review workspace with {len(items)} items.",
            after={"item_count": len(items)},
        )
    )
    return workspace


def _item_from_candidate(
    candidate: MoleculeCandidate,
    *,
    run_id: str,
    disease_name: str,
    target_evidence_counts: dict[str, int],
    config: dict[str, Any],
) -> ReviewItem:
    score = candidate.score_breakdown
    developability = candidate.developability_assessment
    literature = candidate.literature_evidence
    evidence_summary = _evidence_summary(
        score_breakdown=score,
        target_symbols=candidate.known_targets,
        target_evidence_counts=target_evidence_counts,
        molecule_evidence_count=len(candidate.evidence),
        literature=literature,
        warnings=candidate.warnings,
        developability=developability,
        generated_score=None,
    )
    risk_flags = _risk_flags(
        origin="existing",
        warnings=candidate.warnings,
        target_symbols=candidate.known_targets,
        developability=developability,
        literature=literature,
        canonical_smiles=_candidate_smiles(candidate),
    )
    priority = _priority_for_candidate(
        origin="existing",
        score=candidate.score,
        confidence=score.confidence if score is not None else None,
        evidence_summary=evidence_summary,
        risk_flags=risk_flags,
        config=config,
    )
    return ReviewItem(
        run_id=run_id,
        disease_name=disease_name,
        candidate_id=_candidate_id(candidate),
        candidate_name=candidate.name,
        item_type="molecule",
        candidate_origin="existing",
        target_symbols=candidate.known_targets,
        canonical_smiles=_candidate_smiles(candidate),
        score=candidate.score,
        confidence=score.confidence if score is not None else None,
        evidence_summary=evidence_summary,
        literature_summary=_literature_summary(literature),
        developability_summary=_developability_summary(developability),
        generation_summary=None,
        risk_flags=risk_flags,
        warnings=candidate.warnings,
        priority_bucket=priority,
        review_status="pending",
        metadata={
            "source": "ranking_run",
            "identifiers": dict(candidate.identifiers),
            "chemical_metadata": dict(candidate.chemical_metadata),
        },
    )


def _item_from_generated(
    candidate: GeneratedMoleculeHypothesis,
    *,
    run_id: str,
    disease_name: str,
    target_evidence_counts: dict[str, int],
    config: dict[str, Any],
) -> ReviewItem:
    developability = candidate.developability_assessment
    target_symbols = [candidate.target_symbol]
    evidence_summary = _evidence_summary(
        score_breakdown=None,
        target_symbols=target_symbols,
        target_evidence_counts=target_evidence_counts,
        molecule_evidence_count=0,
        literature=None,
        warnings=candidate.warnings,
        developability=developability,
        generated_score=candidate.generation_score,
    )
    risk_flags = _risk_flags(
        origin="generated",
        warnings=candidate.warnings,
        target_symbols=target_symbols,
        developability=developability,
        literature=None,
        canonical_smiles=candidate.canonical_smiles,
    )
    priority = _priority_for_candidate(
        origin="generated",
        score=candidate.generation_score,
        confidence=None,
        evidence_summary=evidence_summary,
        risk_flags=risk_flags,
        config=config,
    )
    return ReviewItem(
        run_id=run_id,
        disease_name=disease_name,
        candidate_id=str(candidate.rank or candidate.name),
        candidate_name=candidate.name,
        item_type="generated_molecule",
        candidate_origin="generated",
        target_symbols=target_symbols,
        canonical_smiles=candidate.canonical_smiles,
        score=candidate.generation_score,
        confidence=None,
        evidence_summary=evidence_summary,
        literature_summary={},
        developability_summary=_developability_summary(developability),
        generation_summary={
            "generation_score": candidate.generation_score,
            "target_symbol": candidate.target_symbol,
            "seed_molecule_names": candidate.seed_molecule_names,
            "source": candidate.source,
            "trace": candidate.trace,
        },
        risk_flags=risk_flags,
        warnings=[
            *candidate.warnings,
            "Generated molecule hypothesis; no direct activity evidence.",
        ],
        priority_bucket=priority,
        review_status="pending",
        metadata={"source": "ranking_run"},
    )


def _priority_for_candidate(
    *,
    origin: str,
    score: float | None,
    confidence: float | None,
    evidence_summary: dict[str, Any],
    risk_flags: list[str],
    config: dict[str, Any],
) -> PriorityBucket:
    require_structure = bool(config.get("require_structure_for_review", False))
    allow_generated_high = bool(config.get("allow_generated_high_priority", False))
    score_value = score or 0.0
    confidence_value = confidence if confidence is not None else 0.0
    literature_counts = evidence_summary["literature_claim_counts"]
    has_contradictory_literature = int(literature_counts.get("contradicts", 0)) > 0
    missing_structure = "missing_structure" in risk_flags
    critical_risk = any(
        flag
        in {
            "critical_developability_risk",
            "severe_safety_warning",
            "invalid_structure",
            "no_target_overlap",
        }
        for flag in risk_flags
    )

    if critical_risk or (require_structure and missing_structure):
        return "reject_suggested"
    if (
        origin == "generated"
        and "generated_no_direct_evidence" in risk_flags
        and any(flag in risk_flags for flag in {"high_developability_risk", "missing_structure"})
    ):
        return "reject_suggested"
    if has_contradictory_literature or missing_structure or "important_missing_data" in risk_flags:
        return "needs_review"
    if origin == "generated":
        if score_value >= 0.7 and evidence_summary["developability_risk_level"] not in {
            "high",
            "critical",
        }:
            return "high_priority" if allow_generated_high else "medium_priority"
        if score_value >= 0.45:
            return "medium_priority"
        return "low_priority"
    if (
        score_value >= 0.75
        and confidence_value >= 0.65
        and evidence_summary["target_evidence_count"] > 0
        and evidence_summary["molecule_evidence_count"] > 0
        and int(literature_counts.get("supports", 0)) > 0
    ):
        return "high_priority"
    if score_value >= 0.45 or confidence_value >= 0.45:
        return "medium_priority"
    return "low_priority"


def _apply_feedback_prior(
    items: list[ReviewItem],
    *,
    config: dict[str, Any],
    disease_name: str,
) -> list[ReviewItem]:
    if not bool(config.get("enable_feedback_prior", False)):
        return items
    feedback_context = config.get("feedback_context")
    feedback = []
    if isinstance(feedback_context, list):
        from molecule_ranker.review.schemas import ExpertFeedback

        feedback = [ExpertFeedback.model_validate(item) for item in feedback_context]
    elif config.get("feedback_db_path"):
        store = FeedbackStore(str(config["feedback_db_path"]))
        disease = (
            disease_name
            if bool(config.get("require_same_disease_for_feedback", True))
            else None
        )
        feedback = store.query(disease=disease)
    return [
        apply_feedback_to_review_item(
            item,
            feedback,
            enable_feedback_prior=True,
            feedback_weight=float(config.get("feedback_weight", 0.05)),
            require_same_disease_for_feedback=bool(
                config.get("require_same_disease_for_feedback", True)
            ),
        )
        for item in items
    ]


def _evidence_summary(
    *,
    score_breakdown: ScoreBreakdown | None,
    target_symbols: list[str],
    target_evidence_counts: dict[str, int],
    molecule_evidence_count: int,
    literature: LiteratureEvidenceBundle | None,
    warnings: list[str],
    developability: DevelopabilityAssessment | None,
    generated_score: float | None,
) -> dict[str, Any]:
    return {
        "score_breakdown": score_breakdown.model_dump(mode="json") if score_breakdown else None,
        "target_evidence_count": sum(
            target_evidence_counts.get(symbol, 0) for symbol in target_symbols
        ),
        "molecule_evidence_count": molecule_evidence_count,
        "literature_claim_counts": _literature_claim_counts(literature),
        "safety_warning_count": _safety_warning_count(warnings, developability),
        "developability_risk_level": _developability_risk_level(developability),
        "generated_score": generated_score,
    }


def _risk_flags(
    *,
    origin: str,
    warnings: list[str],
    target_symbols: list[str],
    developability: DevelopabilityAssessment | None,
    literature: LiteratureEvidenceBundle | None,
    canonical_smiles: str | None,
) -> list[str]:
    flags: list[str] = []
    risk_level = _developability_risk_level(developability)
    if origin == "generated":
        flags.append("generated_no_direct_evidence")
    if risk_level in {"critical", "severe"} or _has_critical_developability(developability):
        flags.append("critical_developability_risk")
    elif risk_level == "high":
        flags.append("high_developability_risk")
    if _has_severe_safety_warning(warnings):
        flags.append("severe_safety_warning")
    if developability is not None and developability.structure_filter_pass is False:
        flags.append("invalid_structure")
    if not canonical_smiles or (
        developability is not None and not developability.structure_available
    ):
        flags.append("missing_structure")
    if not target_symbols:
        flags.append("no_target_overlap")
    if _literature_claim_counts(literature).get("contradicts", 0):
        flags.append("contradictory_literature")
    if literature is not None and literature.absent_reason:
        flags.append("missing_literature")
    return flags


def _literature_claim_counts(literature: LiteratureEvidenceBundle | None) -> dict[str, int]:
    counts = {"supports": 0, "contradicts": 0, "mentions": 0}
    if literature is None:
        return counts
    for item in literature.items:
        for claim in item.claims:
            support = claim.support_level.lower()
            claim_type = claim.claim_type.lower()
            if "contradict" in support or "contradict" in claim_type:
                counts["contradicts"] += 1
            elif "support" in support or "support" in claim_type:
                counts["supports"] += 1
            else:
                counts["mentions"] += 1
    return counts


def _literature_summary(literature: LiteratureEvidenceBundle | None) -> dict[str, Any]:
    if literature is None:
        return {
            "query_count": 0,
            "quality_score": 0.0,
            "claim_counts": _literature_claim_counts(None),
        }
    return {
        "query_count": literature.query_count,
        "quality_score": literature.quality_score,
        "claim_counts": _literature_claim_counts(literature),
        "absent_reason": literature.absent_reason,
    }


def _developability_summary(
    developability: DevelopabilityAssessment | None,
) -> dict[str, Any]:
    if developability is None:
        return {"risk_level": "unknown", "structure_available": None}
    return {
        "risk_level": _developability_risk_level(developability),
        "structure_available": developability.structure_available,
        "structure_filter_pass": developability.structure_filter_pass,
        "triage_recommendation": developability.triage_recommendation,
        "developability_score": developability.developability_score,
        "synthetic_accessibility_score": developability.synthetic_accessibility_score,
    }


def _developability_risk_level(developability: DevelopabilityAssessment | None) -> str:
    if developability is None:
        return "unknown"
    raw = str(developability.metadata.get("risk_level") or "").lower()
    if raw:
        return raw
    if developability.triage_recommendation == "high_risk_flags":
        return "high"
    if developability.triage_recommendation == "review_flags":
        return "medium"
    if developability.triage_recommendation == "insufficient_structure":
        return "unknown"
    return "low"


def _has_critical_developability(developability: DevelopabilityAssessment | None) -> bool:
    if developability is None:
        return False
    if developability.triage_recommendation == "high_risk_flags":
        return True
    all_flags = [
        *developability.admet_property_flags,
        *developability.toxicity_risk_flags,
        *developability.medicinal_chemistry_alerts,
        *developability.chemical_liability_flags,
        *developability.structure_quality_flags,
    ]
    return any(
        flag.severity == "high" and bool(flag.metadata.get("critical", False))
        for flag in all_flags
    )


def _safety_warning_count(
    warnings: list[str],
    developability: DevelopabilityAssessment | None,
) -> int:
    warning_count = sum(1 for warning in warnings if "safety" in warning.lower())
    if developability is None:
        return warning_count
    warning_count += sum(
        1 for flag in developability.toxicity_risk_flags if flag.severity == "high"
    )
    return warning_count


def _has_severe_safety_warning(warnings: list[str]) -> bool:
    return any(
        ("severe" in warning.lower() or "black box" in warning.lower())
        and "safety" in warning.lower()
        for warning in warnings
    )


def _candidate_id(candidate: MoleculeCandidate) -> str:
    return (
        candidate.identifiers.get("chembl")
        or candidate.identifiers.get("pubchem_cid")
        or candidate.name
    )


def _candidate_smiles(candidate: MoleculeCandidate) -> str | None:
    smiles = candidate.chemical_metadata.get("canonical_smiles")
    if smiles is None and candidate.developability_assessment is not None:
        smiles = candidate.developability_assessment.canonical_smiles
    return str(smiles) if smiles is not None else None


def _run_id_from_ranking_run(ranking_run: RankingRun) -> str:
    return f"run-{slugify(ranking_run.disease.canonical_name)}"


def _items_from_existing(
    payload: dict[str, Any],
    run_id: str,
    disease_name: str,
) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for index, raw in enumerate(payload.get("candidates", []), start=1):
        if not isinstance(raw, dict):
            continue
        raw_breakdown = raw.get("score_breakdown")
        breakdown = raw_breakdown if isinstance(raw_breakdown, dict) else {}
        developability = raw.get("developability_summary")
        if not isinstance(developability, dict):
            developability = {}
        raw_evidence = raw.get("evidence")
        evidence = raw_evidence if isinstance(raw_evidence, list) else []
        identifiers = raw.get("identifiers")
        chembl_id = identifiers.get("chembl") if isinstance(identifiers, dict) else None
        known_targets = raw.get("known_targets")
        target_symbols = (
            [str(target) for target in known_targets]
            if isinstance(known_targets, list)
            else []
        )
        warnings = raw.get("warnings")
        warning_list = [str(warning) for warning in warnings] if isinstance(warnings, list) else []
        literature_summary = _dict_or_empty(
            raw.get("literature_summary") or raw.get("literature_evidence")
        )
        claim_counts = _artifact_literature_claim_counts(literature_summary)
        risk_level = str(developability.get("risk_level") or "unknown")
        items.append(
            ReviewItem(
                run_id=run_id,
                disease_name=disease_name,
                candidate_id=str(
                    raw.get("candidate_id")
                    or chembl_id
                    or raw.get("name")
                    or f"candidate-{index}"
                ),
                candidate_name=str(raw.get("name") or f"Candidate {index}"),
                item_type="molecule",
                candidate_origin="existing",
                target_symbols=target_symbols,
                canonical_smiles=_canonical_smiles(raw),
                score=_float_or_none(raw.get("score")),
                confidence=_float_or_none(breakdown.get("confidence")),
                evidence_summary={
                    "score_breakdown": breakdown or None,
                    "target_evidence_count": len(target_symbols),
                    "molecule_evidence_count": len(evidence),
                    "literature_claim_counts": claim_counts,
                    "safety_warning_count": sum(
                        1 for warning in warning_list if "safety" in warning.lower()
                    ),
                    "developability_risk_level": risk_level,
                    "generated_score": None,
                    "items": evidence,
                    "count": len(evidence),
                },
                literature_summary=literature_summary,
                developability_summary=developability,
                generation_summary=None,
                risk_flags=[],
                warnings=warning_list,
                priority_bucket=_priority_bucket(raw.get("score"), warning_list),
                review_status="pending",
            )
        )
    return items


def _items_from_generated(
    payload: dict[str, Any],
    run_id: str,
    disease_name: str,
) -> list[ReviewItem]:
    raw_generated_items = payload.get("generated_molecule_hypotheses") or payload.get(
        "retained_generated_molecules",
        [],
    )
    raw_items = raw_generated_items if isinstance(raw_generated_items, list) else []
    items: list[ReviewItem] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        score = raw.get("generation_score") or raw.get("score")
        candidate_id = str(raw.get("generated_id") or raw.get("name") or f"generated-{index}")
        candidate_name = str(
            raw.get("name") or raw.get("generated_id") or f"Generated {index}"
        )
        smiles = raw.get("canonical_smiles") or raw.get("smiles")
        warnings = raw.get("warnings")
        warning_list = [str(warning) for warning in warnings] if isinstance(warnings, list) else []
        developability = _dict_or_empty(raw.get("developability_summary"))
        risk_level = str(developability.get("risk_level") or "unknown")
        items.append(
            ReviewItem(
                run_id=run_id,
                disease_name=disease_name,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                item_type="generated_molecule",
                candidate_origin="generated",
                target_symbols=_target_symbols(raw),
                canonical_smiles=str(smiles) if smiles is not None else None,
                score=_float_or_none(score),
                confidence=None,
                evidence_summary={
                    "score_breakdown": None,
                    "target_evidence_count": len(_target_symbols(raw)),
                    "molecule_evidence_count": 0,
                    "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
                    "safety_warning_count": sum(
                        1 for warning in warning_list if "safety" in warning.lower()
                    ),
                    "developability_risk_level": risk_level,
                    "generated_score": _float_or_none(score),
                },
                literature_summary={},
                developability_summary=developability,
                generation_summary=raw,
                risk_flags=["generated_no_direct_evidence"],
                warnings=[
                    *warning_list,
                    "Generated molecule hypothesis; no direct activity evidence.",
                ],
                priority_bucket="needs_review",
                review_status="pending",
            )
        )
    return items


def _items_from_biologics_config(
    config: dict[str, Any],
    run_id: str,
    disease_name: str,
) -> list[ReviewItem]:
    payload = config.get("biologics")
    if not isinstance(payload, dict):
        return []
    return _items_from_biologics_payload(
        payload,
        run_id,
        disease_name,
        allow_generic_candidate_keys=True,
    )


def _items_from_biologics_payload(
    payload: dict[str, Any],
    run_id: str,
    disease_name: str,
    *,
    allow_generic_candidate_keys: bool = False,
) -> list[ReviewItem]:
    items = [
        *_items_from_biologic_candidates(
            payload,
            run_id,
            disease_name,
            allow_generic_candidate_keys=allow_generic_candidate_keys,
        ),
        *_items_from_generated_antibodies(
            payload,
            run_id,
            disease_name,
            allow_generic_candidate_keys=allow_generic_candidate_keys,
        ),
    ]
    return items


def _items_from_biologic_candidates(
    payload: dict[str, Any],
    run_id: str,
    disease_name: str,
    *,
    allow_generic_candidate_keys: bool = False,
) -> list[ReviewItem]:
    raw_candidates = payload.get("biologic_candidates")
    if raw_candidates is None and allow_generic_candidate_keys:
        raw_candidates = payload.get("candidates")
    raw_candidates = raw_candidates or []
    raw_sequences = payload.get("antibody_sequences") or payload.get("sequences") or []
    raw_developability = payload.get("antibody_developability") or payload.get(
        "developability",
        [],
    )
    raw_novelty = payload.get("antibody_novelty") or payload.get("novelty") or []
    sequences_by_biologic = _group_by_biologic(raw_sequences)
    developability_by_biologic = _assessment_by_biologic(raw_developability)
    novelty_by_biologic = _assessment_by_biologic(raw_novelty)
    items: list[ReviewItem] = []
    for index, raw in enumerate(raw_candidates, start=1):
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("biologic_id") or f"biologic-{index}")
        biologic_type = str(raw.get("biologic_type") or "other")
        warnings = _string_list(raw.get("warnings"))
        developability = developability_by_biologic.get(candidate_id, {})
        novelty = novelty_by_biologic.get(candidate_id, {})
        sequence_section = {
            "sequence_ids": _string_list(raw.get("sequence_ids")),
            "sequence_count": len(sequences_by_biologic.get(candidate_id, [])),
            "sequences": sequences_by_biologic.get(candidate_id, []),
        }
        score = _float_or_none(
            _dict_or_empty(raw.get("metadata")).get("biologics_score")
            or raw.get("score")
        )
        items.append(
            ReviewItem(
                run_id=run_id,
                disease_name=str(raw.get("disease_name") or disease_name),
                candidate_id=candidate_id,
                candidate_name=str(raw.get("name") or candidate_id),
                item_type="biologic",
                candidate_origin="existing",
                target_symbols=_string_list(raw.get("target_symbols")),
                canonical_smiles=None,
                score=score,
                confidence=_float_or_none(
                    _dict_or_empty(
                        _dict_or_empty(raw.get("metadata")).get(
                            "biologics_score_components"
                        )
                    ).get("effective_confidence")
                ),
                evidence_summary={
                    "target_evidence_count": len(_string_list(raw.get("target_symbols"))),
                    "molecule_evidence_count": len(_string_list(raw.get("evidence_item_ids"))),
                    "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
                    "safety_warning_count": sum(
                        1 for warning in warnings if "safety" in warning.lower()
                    ),
                    "developability_risk_level": _biologic_developability_risk(developability),
                    "generated_score": None,
                    "direct_experimental_evidence": bool(
                        raw.get("direct_experimental_evidence")
                    ),
                },
                literature_summary={},
                developability_summary=developability,
                generation_summary=None,
                risk_flags=_biologic_risk_flags(developability, novelty, generated=False),
                warnings=warnings,
                priority_bucket=_priority_bucket(score, warnings),
                review_status="needs_expert_review",
                metadata={
                    "source": "biologics",
                    "biologic_type": biologic_type,
                    "antibody_sequence": sequence_section,
                    "antibody_novelty": novelty,
                    "antibody_developability": developability,
                    "required_expert_roles": _biologics_required_roles(),
                    "generated_antibody_warning": None,
                },
            )
        )
    return items


def _items_from_generated_antibodies(
    payload: dict[str, Any],
    run_id: str,
    disease_name: str,
    *,
    allow_generic_candidate_keys: bool = False,
) -> list[ReviewItem]:
    raw_generated = payload.get("generated_antibody_hypotheses") or payload.get(
        "generated_antibodies"
    )
    if raw_generated is None and allow_generic_candidate_keys:
        raw_generated = payload.get("generated")
    raw_generated = raw_generated or []
    items: list[ReviewItem] = []
    for index, raw in enumerate(raw_generated if isinstance(raw_generated, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("generated_antibody_id") or f"generated-antibody-{index}")
        metadata = _dict_or_empty(raw.get("metadata"))
        developability = _dict_or_empty(metadata.get("developability"))
        novelty = _dict_or_empty(metadata.get("novelty"))
        validation = _dict_or_empty(metadata.get("validation"))
        warnings = [
            *_string_list(raw.get("warnings")),
            "Generated antibody hypothesis; no direct binding, activity, safety, "
            "developability, manufacturability, or direct experimental evidence claim.",
        ]
        score = _float_or_none(raw.get("score"))
        items.append(
            ReviewItem(
                run_id=run_id,
                disease_name=disease_name,
                candidate_id=candidate_id,
                candidate_name=candidate_id,
                item_type="generated_antibody",
                candidate_origin="generated",
                target_symbols=_string_list(raw.get("target_symbols")),
                canonical_smiles=None,
                score=score,
                confidence=_float_or_none(raw.get("confidence")),
                evidence_summary={
                    "target_evidence_count": len(_string_list(raw.get("target_symbols"))),
                    "molecule_evidence_count": 0,
                    "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
                    "safety_warning_count": 0,
                    "developability_risk_level": _biologic_developability_risk(developability),
                    "generated_score": score,
                    "direct_experimental_evidence": False,
                },
                literature_summary={},
                developability_summary=developability,
                generation_summary=raw,
                risk_flags=_biologic_risk_flags(developability, novelty, generated=True),
                warnings=warnings,
                priority_bucket="needs_review",
                review_status="needs_expert_review",
                metadata={
                    "source": "biologics",
                    "biologic_type": "generated_antibody",
                    "antibody_sequence": {
                        "sequence_ids": _string_list(raw.get("generated_sequence_ids")),
                        "validation": validation,
                        "generated_sequences": metadata.get("generated_sequences") or [],
                    },
                    "antibody_novelty": novelty,
                    "antibody_developability": developability,
                    "required_expert_roles": _biologics_required_roles(),
                    "generated_antibody_warning": warnings[-1],
                    "review_gate_required": True,
                },
            )
        )
    return items


def _biologics_required_roles() -> list[str]:
    return ["biologics scientist", "antibody engineer", "developability expert"]


def _group_by_biologic(raw_items: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    items = raw_items if isinstance(raw_items, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        biologic_id = str(item.get("biologic_id") or "")
        if not biologic_id:
            continue
        grouped.setdefault(biologic_id, []).append(item)
    return grouped


def _assessment_by_biologic(raw_items: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("assessments") or raw_items.get("items") or []
    items = raw_items if isinstance(raw_items, list) else []
    by_biologic: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        biologic_id = str(item.get("biologic_id") or "")
        if biologic_id:
            by_biologic[biologic_id] = item
    return by_biologic


def _biologic_developability_risk(developability: dict[str, Any]) -> str:
    if not developability:
        return "unknown"
    risks = [
        str(developability.get(key) or "unknown")
        for key in (
            "aggregation_risk",
            "polyreactivity_risk",
            "immunogenicity_risk",
            "viscosity_risk",
            "stability_risk",
            "expression_risk",
        )
    ]
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    if all(risk == "low" for risk in risks):
        return "low"
    return "unknown"


def _biologic_risk_flags(
    developability: dict[str, Any],
    novelty: dict[str, Any],
    *,
    generated: bool,
) -> list[str]:
    flags: list[str] = []
    if generated:
        flags.extend(["generated_no_direct_evidence", "generated_antibody_requires_review"])
    risk = _biologic_developability_risk(developability)
    if risk == "high":
        flags.append("high_antibody_developability_risk")
    sequence_flags = _string_list(developability.get("sequence_liability_flags"))
    cdr_flags = _string_list(developability.get("cdr_liability_flags"))
    if sequence_flags or cdr_flags:
        flags.append("sequence_liability_risk")
    if str(novelty.get("novelty_class") or "") in {"known", "near_duplicate"}:
        flags.append("antibody_novelty_review")
    return flags


def _canonical_smiles(raw: dict[str, Any]) -> str | None:
    chemical = raw.get("chemical_metadata")
    if isinstance(chemical, dict):
        smiles = chemical.get("canonical_smiles")
        return str(smiles) if smiles is not None else None
    return None


def _target_symbols(raw: dict[str, Any]) -> list[str]:
    if "target_symbol" in raw:
        return [str(raw["target_symbol"])]
    targets = raw.get("conditioned_targets")
    return [str(target) for target in targets] if isinstance(targets, list) else []


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _artifact_literature_claim_counts(summary: dict[str, Any]) -> dict[str, int]:
    raw_counts = summary.get("claim_counts") or summary.get("literature_claim_counts")
    if not isinstance(raw_counts, dict):
        return {"supports": 0, "contradicts": 0, "mentions": 0}
    return {
        "supports": int(raw_counts.get("supports", 0) or 0),
        "contradicts": int(raw_counts.get("contradicts", 0) or 0),
        "mentions": int(raw_counts.get("mentions", 0) or 0),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _priority_bucket(score: Any, warnings: Any) -> PriorityBucket:
    parsed = _float_or_none(score)
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    if warning_count >= 3:
        return "needs_review"
    if parsed is None:
        return "needs_review"
    if parsed >= 0.7:
        return "high_priority"
    if parsed >= 0.45:
        return "medium_priority"
    return "low_priority"
