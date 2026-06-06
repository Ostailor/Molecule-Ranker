from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from molecule_ranker.biologics.schemas import (
    AntibodyDevelopabilityAssessment,
    AntibodyRiskLevel,
    AntibodySequence,
)

HEURISTIC_DEVELOPABILITY_WARNING = (
    "Antibody developability assessment uses deterministic heuristic triage "
    "signals only; it does not establish clinical, immunogenicity, expression, "
    "stability, or manufacturing outcomes."
)
GENERATED_SEQUENCE_WARNING = (
    "Generated antibody sequences are computational hypotheses only and require "
    "deterministic validation, novelty checks, developability triage, review "
    "gates, and source-linked experimental results before any evidence claim."
)

GLYCOSYLATION_RE = re.compile(r"N[^P][ST]")
DEAMIDATION_RE = re.compile(r"(NG|NS|NN|NQ|DG)")
OXIDATION_RE = re.compile(r"[MW]")
CLIPPING_RE = re.compile(r"(DP|DG|NS|KK|RR|KR|RK)")
HYDROPHOBIC_RUN_RE = re.compile(r"[AILMFWVY]{6,}")
AROMATIC_RUN_RE = re.compile(r"[FWY]{3,}")

HYDROPHOBIC_RESIDUES = frozenset("AILMFWVY")
CHARGED_POSITIVE = frozenset("KRH")
CHARGED_NEGATIVE = frozenset("DE")


def assess_antibody_developability(
    *,
    assessment_id: str,
    biologic_id: str,
    sequences: list[AntibodySequence],
    external_model_assessment: Mapping[str, Any] | None = None,
) -> AntibodyDevelopabilityAssessment:
    """Assess antibody developability with conservative heuristic triage.

    These signals are review prioritization heuristics. They must not be used as
    proof that an antibody is safe, manufacturable, stable, expressible, or has
    low immunogenicity.
    """

    sequence_flags: list[str] = []
    cdr_flags: list[str] = []
    warnings = [HEURISTIC_DEVELOPABILITY_WARNING]
    risk_points: dict[str, int] = {
        "aggregation": 0,
        "polyreactivity": 0,
        "immunogenicity": 0,
        "viscosity": 0,
        "stability": 0,
        "expression": 0,
    }

    for sequence in sequences:
        seq = _normalized_sequence(sequence.amino_acid_sequence)
        metrics = _sequence_metrics(seq)
        _assess_sequence_liabilities(
            sequence,
            normalized_sequence=seq,
            metrics=metrics,
            sequence_flags=sequence_flags,
            cdr_flags=cdr_flags,
            warnings=warnings,
            risk_points=risk_points,
        )

    if not sequences:
        warnings.append("No antibody sequences supplied; sequence-specific triage unavailable.")

    validated_external_model = bool(
        external_model_assessment
        and external_model_assessment.get("validated_external_model") is True
    )
    if external_model_assessment:
        sequence_flags.extend(_external_model_flags(external_model_assessment))
        if not validated_external_model:
            warnings.append(
                "External developability model output was not marked as validated; "
                "confidence remains conservative."
            )

    risk_by_category: dict[str, AntibodyRiskLevel] = {
        category: _risk_from_points(points, unknown=not sequences)
        for category, points in risk_points.items()
    }
    score = _overall_score(risk_by_category, sequence_flags, cdr_flags, sequences)
    confidence = _confidence(
        sequences=sequences,
        validated_external_model=validated_external_model,
    )

    return AntibodyDevelopabilityAssessment(
        assessment_id=assessment_id,
        biologic_id=biologic_id,
        sequence_ids=[sequence.sequence_id for sequence in sequences],
        aggregation_risk=risk_by_category["aggregation"],
        polyreactivity_risk=risk_by_category["polyreactivity"],
        immunogenicity_risk=risk_by_category["immunogenicity"],
        viscosity_risk=risk_by_category["viscosity"],
        stability_risk=risk_by_category["stability"],
        expression_risk=risk_by_category["expression"],
        sequence_liability_flags=sorted(set(sequence_flags)),
        cdr_liability_flags=sorted(set(cdr_flags)),
        overall_developability_score=score,
        confidence=confidence,
        warnings=sorted(set(warnings)),
        metadata={
            "assessment_type": "deterministic_antibody_heuristic_triage",
            "validated_external_model": validated_external_model,
            "sequence_count": len(sequences),
            "risk_points": risk_points,
        },
    )


def _assess_sequence_liabilities(
    sequence: AntibodySequence,
    *,
    normalized_sequence: str,
    metrics: dict[str, float],
    sequence_flags: list[str],
    cdr_flags: list[str],
    warnings: list[str],
    risk_points: dict[str, int],
) -> None:
    prefix = sequence.sequence_id
    if sequence.is_generated:
        warnings.append(GENERATED_SEQUENCE_WARNING)
        sequence_flags.append(f"{prefix}: generated sequence requires review gate")

    if GLYCOSYLATION_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: potential N-linked glycosylation motif")
        risk_points["stability"] += 1
        risk_points["immunogenicity"] += 1
    if DEAMIDATION_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: deamidation-prone motif")
        risk_points["stability"] += 2
    if OXIDATION_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: oxidation-prone methionine/tryptophan residue")
        risk_points["stability"] += 1
    if CLIPPING_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: clipping or proteolysis-liability motif")
        risk_points["stability"] += 1
        risk_points["expression"] += 1
    if HYDROPHOBIC_RUN_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: hydrophobic run")
        risk_points["aggregation"] += 2
        risk_points["polyreactivity"] += 1
    if AROMATIC_RUN_RE.search(normalized_sequence):
        sequence_flags.append(f"{prefix}: aromatic run")
        risk_points["aggregation"] += 1
        risk_points["polyreactivity"] += 1

    cysteine_count = normalized_sequence.count("C")
    if cysteine_count == 0:
        sequence_flags.append(f"{prefix}: no cysteine residues detected")
        risk_points["expression"] += 1
    if cysteine_count % 2 == 1:
        sequence_flags.append(f"{prefix}: unpaired cysteine count")
        warnings.append(
            f"{prefix}: unpaired cysteine warning; disulfide pairing requires review."
        )
        risk_points["aggregation"] += 1
        risk_points["expression"] += 2
    if cysteine_count > 8:
        sequence_flags.append(f"{prefix}: high cysteine count")
        risk_points["aggregation"] += 1
        risk_points["expression"] += 1

    if metrics["hydrophobic_fraction"] > 0.45:
        sequence_flags.append(f"{prefix}: unusual hydrophobicity")
        risk_points["aggregation"] += 2
        risk_points["polyreactivity"] += 1
    if abs(metrics["net_charge_fraction"]) > 0.18:
        sequence_flags.append(f"{prefix}: unusual net charge")
        risk_points["viscosity"] += 2
        risk_points["polyreactivity"] += 1
    if metrics["positive_charge_fraction"] > 0.20:
        sequence_flags.append(f"{prefix}: unusually high positive charge")
        risk_points["viscosity"] += 2
        risk_points["polyreactivity"] += 2
    if sequence.chain_type == "unknown":
        sequence_flags.append(f"{prefix}: unknown chain type limits developability triage")
        risk_points["expression"] += 1
    if sequence.sequence_length < 80 or sequence.sequence_length > 320:
        sequence_flags.append(f"{prefix}: unusual antibody sequence length")
        risk_points["expression"] += 1
        risk_points["stability"] += 1

    _assess_cdr_liabilities(
        sequence,
        normalized_sequence=normalized_sequence,
        cdr_flags=cdr_flags,
        risk_points=risk_points,
    )


def _assess_cdr_liabilities(
    sequence: AntibodySequence,
    *,
    normalized_sequence: str,
    cdr_flags: list[str],
    risk_points: dict[str, int],
) -> None:
    prefix = sequence.sequence_id
    cdr_lengths = sequence.metadata.get("cdr_lengths")
    cdr_sequences = sequence.metadata.get("cdr_sequences")
    cdr3_length = _int_or_none(cdr_lengths.get("cdr3")) if isinstance(cdr_lengths, dict) else None
    cdr3_sequence = None
    if isinstance(cdr_sequences, Mapping):
        cdr3_sequence = cdr_sequences.get("cdr3")
    if isinstance(cdr3_sequence, str):
        if cdr3_length is None:
            cdr3_length = len(cdr3_sequence)
        cdr3_metrics = _sequence_metrics(cdr3_sequence)
        if cdr3_metrics["hydrophobic_fraction"] > 0.50:
            cdr_flags.append(f"{prefix}: hydrophobic CDR3")
            risk_points["aggregation"] += 1
            risk_points["polyreactivity"] += 1
        if abs(cdr3_metrics["net_charge_fraction"]) > 0.25:
            cdr_flags.append(f"{prefix}: highly charged CDR3")
            risk_points["polyreactivity"] += 1
            risk_points["viscosity"] += 1

    if cdr3_length is not None and (cdr3_length < 5 or cdr3_length > 30):
        cdr_flags.append(f"{prefix}: unusual CDR3 length")
        risk_points["aggregation"] += 1
        risk_points["polyreactivity"] += 1
        risk_points["immunogenicity"] += 1

    for label, cdr_sequence in _cdr_sequences(cdr_sequences):
        if GLYCOSYLATION_RE.search(cdr_sequence):
            cdr_flags.append(f"{prefix}: {label} glycosylation motif")
            risk_points["stability"] += 1
        if DEAMIDATION_RE.search(cdr_sequence):
            cdr_flags.append(f"{prefix}: {label} deamidation-prone motif")
            risk_points["stability"] += 1
        if HYDROPHOBIC_RUN_RE.search(cdr_sequence):
            cdr_flags.append(f"{prefix}: {label} hydrophobic run")
            risk_points["aggregation"] += 1
            risk_points["polyreactivity"] += 1

    if not isinstance(cdr_lengths, dict) and not isinstance(cdr_sequences, Mapping):
        if sequence.sequence_length < 90:
            cdr_flags.append(f"{prefix}: sequence too short for CDR triage")
        elif normalized_sequence:
            cdr_flags.append(f"{prefix}: CDR annotations unavailable for CDR liability triage")


def _sequence_metrics(sequence: str) -> dict[str, float]:
    if not sequence:
        return {
            "hydrophobic_fraction": 0.0,
            "positive_charge_fraction": 0.0,
            "negative_charge_fraction": 0.0,
            "net_charge_fraction": 0.0,
        }
    length = len(sequence)
    positive = sum(1 for residue in sequence if residue in CHARGED_POSITIVE)
    negative = sum(1 for residue in sequence if residue in CHARGED_NEGATIVE)
    return {
        "hydrophobic_fraction": sum(
            1 for residue in sequence if residue in HYDROPHOBIC_RESIDUES
        )
        / length,
        "positive_charge_fraction": positive / length,
        "negative_charge_fraction": negative / length,
        "net_charge_fraction": (positive - negative) / length,
    }


def _risk_from_points(points: int, *, unknown: bool) -> AntibodyRiskLevel:
    if unknown:
        return "unknown"
    if points >= 4:
        return "high"
    if points >= 2:
        return "medium"
    return "low"


def _overall_score(
    risk_by_category: Mapping[str, AntibodyRiskLevel],
    sequence_flags: list[str],
    cdr_flags: list[str],
    sequences: list[AntibodySequence],
) -> float:
    if not sequences:
        return 0.0
    risk_penalty = {
        "unknown": 0.18,
        "low": 0.0,
        "medium": 0.12,
        "high": 0.25,
    }
    penalty = sum(risk_penalty[risk] for risk in risk_by_category.values())
    penalty += min(0.20, 0.015 * (len(sequence_flags) + len(cdr_flags)))
    return round(max(0.0, min(1.0, 0.86 - penalty)), 3)


def _confidence(
    *,
    sequences: list[AntibodySequence],
    validated_external_model: bool,
) -> float:
    if validated_external_model:
        return 0.72
    if not sequences:
        return 0.2
    if all(sequence.metadata.get("cdr_lengths") for sequence in sequences):
        return 0.5
    return 0.42


def _external_model_flags(external_model_assessment: Mapping[str, Any]) -> list[str]:
    flags = external_model_assessment.get("liability_flags")
    if isinstance(flags, str):
        return [f"external_model: {flags}"]
    if isinstance(flags, list):
        return [f"external_model: {flag}" for flag in flags if isinstance(flag, str)]
    return []


def _cdr_sequences(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, Mapping):
        return []
    sequences: list[tuple[str, str]] = []
    for label in ("cdr1", "cdr2", "cdr3"):
        sequence = value.get(label)
        if isinstance(sequence, str) and sequence:
            sequences.append((label.upper(), _normalized_sequence(sequence)))
    return sequences


def _normalized_sequence(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "GENERATED_SEQUENCE_WARNING",
    "HEURISTIC_DEVELOPABILITY_WARNING",
    "assess_antibody_developability",
]
