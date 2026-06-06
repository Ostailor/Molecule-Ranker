from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.biologics.schemas import (
    AntibodyNumbering,
    AntibodyNumberingScheme,
    AntibodySequence,
    CDRAnnotation,
)

LOW_CONFIDENCE_THRESHOLD = 0.6
NumberingAdapter = Callable[[AntibodySequence, AntibodyNumberingScheme], Mapping[str, Any]]

_CONFIGURED_ADAPTER: NumberingAdapter | None = None


def configure_numbering_adapter(adapter: NumberingAdapter | None) -> None:
    """Configure an optional external numbering adapter for tests or deployment wiring."""

    global _CONFIGURED_ADAPTER
    _CONFIGURED_ADAPTER = adapter


def number_antibody_sequence(
    sequence: AntibodySequence,
    scheme: AntibodyNumberingScheme = "imgt",
) -> AntibodyNumbering:
    """Number an antibody sequence using an optional external tool or a fallback.

    External numbering is optional. If no configured adapter is present and ANARCI is
    not explicitly enabled/configured, the function returns a low-confidence heuristic
    numbering artifact. If external numbering fails, the scheme is marked `unknown`
    and review is required.
    """

    try:
        payload = _external_numbering_payload(sequence, scheme)
    except Exception as exc:
        return _failed_numbering(sequence, scheme, exc)
    if payload is not None:
        return _numbering_from_payload(sequence, scheme, payload)
    return _heuristic_numbering(sequence, scheme)


def annotate_cdrs(sequence: AntibodySequence, numbering: AntibodyNumbering) -> CDRAnnotation:
    warnings = list(numbering.warnings)
    if numbering.confidence < LOW_CONFIDENCE_THRESHOLD:
        warnings.append(
            "CDR annotation withheld because numbering confidence is low; expert review required."
        )
        return CDRAnnotation(
            annotation_id=_stable_id("cdr", sequence.sequence_id, numbering.scheme),
            sequence_id=sequence.sequence_id,
            scheme=numbering.scheme,
            cdr1=None,
            cdr2=None,
            cdr3=None,
            cdr_lengths={},
            unusual_motifs=[],
            warnings=warnings,
            metadata={
                "numbering_id": numbering.numbering_id,
                "precise_cdrs_withheld": True,
                "confidence": numbering.confidence,
            },
        )

    cdrs = {
        name: _slice_region(sequence.amino_acid_sequence, region)
        for name, region in numbering.cdr_regions.items()
        if _region_in_bounds(region, sequence.sequence_length)
    }
    return CDRAnnotation(
        annotation_id=_stable_id("cdr", sequence.sequence_id, numbering.scheme),
        sequence_id=sequence.sequence_id,
        scheme=numbering.scheme,
        cdr1=cdrs.get("cdr1"),
        cdr2=cdrs.get("cdr2"),
        cdr3=cdrs.get("cdr3"),
        cdr_lengths={name: len(value) for name, value in cdrs.items()},
        unusual_motifs=_unusual_motifs(cdrs),
        warnings=warnings,
        metadata={
            "numbering_id": numbering.numbering_id,
            "confidence": numbering.confidence,
            "numbering_tool": numbering.numbering_tool,
        },
    )


def validate_cdr_regions(annotation: CDRAnnotation) -> list[str]:
    findings: list[str] = []
    for cdr_name, sequence in {
        "cdr1": annotation.cdr1,
        "cdr2": annotation.cdr2,
        "cdr3": annotation.cdr3,
    }.items():
        expected_length = annotation.cdr_lengths.get(cdr_name)
        if sequence is None:
            findings.append(f"{cdr_name} missing or withheld")
            continue
        if expected_length is None:
            findings.append(f"{cdr_name} length metadata missing")
        elif expected_length != len(sequence):
            findings.append(f"{cdr_name} length metadata does not match sequence")
    cdr3_length = annotation.cdr_lengths.get("cdr3")
    if cdr3_length is not None and (cdr3_length < 5 or cdr3_length > 30):
        findings.append("cdr3 length is outside broad antibody review bounds")
    return findings


def annotate_antibody_numbering(
    sequence: AntibodySequence,
    *,
    scheme: AntibodyNumberingScheme = "imgt",
    numbering_tool: str | None = None,
) -> tuple[AntibodyNumbering, CDRAnnotation]:
    """Backward-compatible wrapper returning numbering and CDR annotation."""

    numbering = number_antibody_sequence(sequence, scheme=scheme)
    if numbering_tool is not None and numbering.numbering_tool == "internal_heuristic":
        numbering = numbering.model_copy(update={"numbering_tool": numbering_tool})
    return numbering, annotate_cdrs(sequence, numbering)


def _external_numbering_payload(
    sequence: AntibodySequence,
    scheme: AntibodyNumberingScheme,
) -> Mapping[str, Any] | None:
    if _CONFIGURED_ADAPTER is not None:
        return _CONFIGURED_ADAPTER(sequence, scheme)
    if os.getenv("MOLECULE_RANKER_ENABLE_ANARCI", "").lower() not in {"1", "true", "yes"}:
        return None
    return _run_anarci_if_available(sequence, scheme)


def _run_anarci_if_available(
    sequence: AntibodySequence,
    scheme: AntibodyNumberingScheme,
) -> Mapping[str, Any] | None:
    try:
        module = importlib.import_module("anarci")
    except ImportError:
        return None
    runner = getattr(module, "run_anarci", None)
    if not callable(runner):
        return None
    raw = runner([(sequence.sequence_id, sequence.amino_acid_sequence)], scheme=scheme)
    parser = getattr(module, "parse_molecule_ranker_payload", None)
    if callable(parser):
        parsed = parser(raw)
        if isinstance(parsed, Mapping):
            return parsed
    return {
        "numbering_tool": "anarci",
        "tool_version": str(getattr(module, "__version__", "unknown")),
        "scheme": scheme,
        "confidence": 0.7,
        "framework_regions": {},
        "cdr_regions": {},
        "insertions": {},
        "warnings": [
            "ANARCI output parser unavailable; precise CDR regions require review."
        ],
        "raw_output_present": raw is not None,
    }


def _numbering_from_payload(
    sequence: AntibodySequence,
    requested_scheme: AntibodyNumberingScheme,
    payload: Mapping[str, Any],
) -> AntibodyNumbering:
    scheme = _scheme(payload.get("scheme"), fallback=requested_scheme)
    confidence = _float(payload.get("confidence"), fallback=0.0)
    warnings = [str(item) for item in payload.get("warnings", []) or []]
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        warnings.append("Numbering confidence is low; precise CDRs require review.")
    return AntibodyNumbering(
        numbering_id=str(
            payload.get("numbering_id")
            or _stable_id("numbering", sequence.sequence_id, scheme)
        ),
        sequence_id=sequence.sequence_id,
        scheme=scheme,
        framework_regions=_regions(payload.get("framework_regions")),
        cdr_regions=_regions(payload.get("cdr_regions")),
        insertions=dict(payload.get("insertions") or {}),
        numbering_tool=str(payload.get("numbering_tool") or "external_numbering_adapter"),
        confidence=confidence,
        warnings=warnings,
        metadata={
            "tool_version": payload.get("tool_version"),
            "external_numbering": True,
            "requested_scheme": requested_scheme,
        },
    )


def _heuristic_numbering(
    sequence: AntibodySequence,
    scheme: AntibodyNumberingScheme,
) -> AntibodyNumbering:
    length = sequence.sequence_length
    cdr_regions: dict[str, tuple[int, int]] = {}
    framework_regions: dict[str, tuple[int, int]] = {}
    warnings = [
        "External antibody numbering unavailable; internal heuristic fallback used.",
        "Heuristic numbering is low confidence and requires expert review.",
    ]
    if length >= 70:
        cdr_regions = {
            "cdr1": (27, min(38, length)),
            "cdr2": (56, min(65, length)),
            "cdr3": (105, min(117, length)) if length >= 105 else (length, length),
        }
        framework_regions = {
            "fr1": (1, 26),
            "fr2": (39, min(55, length)),
            "fr3": (66, min(104, length)),
        }
    else:
        warnings.append("Sequence is too short for heuristic CDR region annotation.")
    return AntibodyNumbering(
        numbering_id=_stable_id("numbering", sequence.sequence_id, scheme),
        sequence_id=sequence.sequence_id,
        scheme=scheme,
        framework_regions=framework_regions,
        cdr_regions=cdr_regions,
        insertions={},
        numbering_tool="internal_heuristic",
        confidence=0.35,
        warnings=warnings,
        metadata={
            "tool_version": "molecule_ranker_internal.v1",
            "external_numbering": False,
            "requested_scheme": scheme,
            "review_required": True,
        },
    )


def _failed_numbering(
    sequence: AntibodySequence,
    requested_scheme: AntibodyNumberingScheme,
    exc: Exception,
) -> AntibodyNumbering:
    return AntibodyNumbering(
        numbering_id=_stable_id("numbering", sequence.sequence_id, "unknown"),
        sequence_id=sequence.sequence_id,
        scheme="unknown",
        framework_regions={},
        cdr_regions={},
        insertions={},
        numbering_tool="numbering_unavailable",
        confidence=0.0,
        warnings=[
            "Antibody numbering failed; expert review required.",
            str(exc),
        ],
        metadata={
            "requested_scheme": requested_scheme,
            "external_numbering": _CONFIGURED_ADAPTER is not None,
            "review_required": True,
        },
    )


def _slice_region(sequence: str, region: tuple[int, int]) -> str:
    start, end = region
    return sequence[start - 1 : end]


def _region_in_bounds(region: tuple[int, int], sequence_length: int) -> bool:
    start, end = region
    return start >= 1 and start <= end <= sequence_length


def _regions(value: Any) -> dict[str, tuple[int, int]]:
    if not isinstance(value, Mapping):
        return {}
    regions: dict[str, tuple[int, int]] = {}
    for key, raw_region in value.items():
        if not isinstance(raw_region, (list, tuple)) or len(raw_region) != 2:
            continue
        start = _int(raw_region[0])
        end = _int(raw_region[1])
        if start is None or end is None or start < 1 or end < start:
            continue
        regions[str(key)] = (start, end)
    return regions


def _unusual_motifs(cdrs: Mapping[str, str]) -> list[str]:
    motifs: list[str] = []
    for cdr_name, sequence in cdrs.items():
        if "NG" in sequence or "NS" in sequence:
            motifs.append(f"{cdr_name}: deamidation motif")
        if "M" in sequence or "W" in sequence:
            motifs.append(f"{cdr_name}: oxidation-prone residue")
    return motifs


def _stable_id(prefix: str, sequence_id: str, scheme: str) -> str:
    digest = uuid5(NAMESPACE_URL, f"{prefix}:{sequence_id}:{scheme}").hex[:16]
    return f"{prefix}-{digest}"


def _scheme(value: Any, *, fallback: AntibodyNumberingScheme) -> AntibodyNumberingScheme:
    if value in {"imgt", "chothia", "kabat", "aho", "unknown"}:
        return value
    return fallback


def _float(value: Any, *, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(parsed, 1.0))


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "NumberingAdapter",
    "annotate_antibody_numbering",
    "annotate_cdrs",
    "configure_numbering_adapter",
    "number_antibody_sequence",
    "validate_cdr_regions",
]
