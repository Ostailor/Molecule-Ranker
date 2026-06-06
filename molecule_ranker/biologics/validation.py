from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from molecule_ranker.biologics.schemas import (
    ALLOWED_AMINO_ACIDS,
    AntibodyChainType,
    AntibodySequence,
)

AMBIGUOUS_RESIDUES = frozenset("XBZJUO")
STOP_RESIDUES = frozenset("*")
NUCLEOTIDE_RESIDUES = frozenset("ACGTU")
HEURISTIC_LIMITATION = (
    "Antibody sequence validation uses deterministic heuristic checks only and "
    "does not prove safety, stability, expression, or manufacturability."
)

CHAIN_LENGTH_BOUNDS: dict[AntibodyChainType, tuple[int, int]] = {
    "heavy": (90, 150),
    "light_kappa": (80, 140),
    "light_lambda": (80, 140),
    "paired_heavy_light": (170, 320),
    "single_domain_vhh": (90, 150),
    "scfv": (180, 320),
    "unknown": (20, 2_000),
}

GLYCOSYLATION_RE = re.compile(r"N[^P][ST]")
DEAMIDATION_RE = re.compile(r"(NG|NS|NN|NQ|DG)")
OXIDATION_RE = re.compile(r"[MW]")
CLIPPING_RE = re.compile(r"(DP|DG|NS|KK|RR|KR|RK)")
HYDROPHOBIC_RUN_RE = re.compile(r"[AILMFWVY]{6,}")


def validate_antibody_sequence(
    sequence: AntibodySequence,
    *,
    allow_ambiguous: bool = False,
    existing_sequences: Iterable[AntibodySequence] | None = None,
) -> dict[str, Any]:
    """Validate an antibody sequence with deterministic heuristic checks.

    The result is an operational triage artifact. It must not be interpreted as
    proof that a sequence is safe, stable, expressible, developable, or
    manufacturable.
    """

    normalized = _normalized_sequence(sequence.amino_acid_sequence)
    errors: list[str] = []
    warnings = list(sequence.metadata.get("warnings", []))
    liability_flags: list[str] = []

    _check_alphabet(
        normalized,
        errors=errors,
        warnings=warnings,
        allow_ambiguous=allow_ambiguous,
    )
    _check_nucleotide_like(normalized, errors=errors)
    _check_stop_codons(normalized, errors=errors)
    _check_length(sequence.chain_type, len(normalized), errors=errors, warnings=warnings)
    _check_chain_type_plausibility(sequence, normalized, warnings=warnings)
    _check_cysteines(normalized, warnings=warnings)
    _check_liability_motifs(normalized, warnings=warnings, liability_flags=liability_flags)
    _check_unusual_cdr_lengths(sequence, warnings=warnings)

    duplicated_sequence_ids = _duplicate_sequence_ids(sequence, normalized, existing_sequences)
    if duplicated_sequence_ids:
        warnings.append(
            "Duplicate exact sequence detected in supplied comparison set: "
            + ", ".join(duplicated_sequence_ids)
        )

    if sequence.is_generated:
        warnings.append("Generated antibody sequences are computational hypotheses only.")

    valid = not errors
    rejected = sequence.is_generated and not valid
    return {
        "sequence_id": sequence.sequence_id,
        "valid": valid,
        "rejected": rejected,
        "sequence_length": len(normalized),
        "chain_type": sequence.chain_type,
        "is_generated": sequence.is_generated,
        "errors": errors,
        "warnings": warnings,
        "liability_flags": liability_flags,
        "duplicated_sequence_ids": duplicated_sequence_ids,
        "allow_ambiguous": allow_ambiguous,
        "deterministic": True,
        "limitations": [HEURISTIC_LIMITATION],
    }


def validate_antibody_sequences(
    sequences: Iterable[AntibodySequence],
    *,
    allow_ambiguous: bool = False,
) -> list[dict[str, Any]]:
    sequence_list = list(sequences)
    return [
        validate_antibody_sequence(
            sequence,
            allow_ambiguous=allow_ambiguous,
            existing_sequences=[
                other for other in sequence_list if other.sequence_id != sequence.sequence_id
            ],
        )
        for sequence in sequence_list
    ]


def _normalized_sequence(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _check_alphabet(
    sequence: str,
    *,
    errors: list[str],
    warnings: list[str],
    allow_ambiguous: bool,
) -> None:
    allowed = set(ALLOWED_AMINO_ACIDS) | set(STOP_RESIDUES)
    if allow_ambiguous:
        allowed |= set(AMBIGUOUS_RESIDUES)
    invalid = sorted(set(sequence) - allowed)
    if invalid:
        errors.append("invalid amino acid characters: " + ", ".join(invalid))
    ambiguous = sorted(set(sequence) & AMBIGUOUS_RESIDUES)
    if ambiguous:
        message = "ambiguous residues present: " + ", ".join(ambiguous)
        if allow_ambiguous:
            warnings.append(message)
        else:
            errors.append(message)


def _check_nucleotide_like(sequence: str, *, errors: list[str]) -> None:
    if len(sequence) < 20:
        return
    nucleotide_count = sum(1 for residue in sequence if residue in NUCLEOTIDE_RESIDUES)
    if nucleotide_count / len(sequence) >= 0.95:
        errors.append("nucleotide-like sequence is not accepted as a protein sequence")


def _check_stop_codons(sequence: str, *, errors: list[str]) -> None:
    if "*" in sequence:
        errors.append("stop codon marker '*' is not allowed in antibody protein sequences")


def _check_length(
    chain_type: AntibodyChainType,
    sequence_length: int,
    *,
    errors: list[str],
    warnings: list[str],
) -> None:
    minimum, maximum = CHAIN_LENGTH_BOUNDS[chain_type]
    if sequence_length < minimum:
        errors.append(
            f"{chain_type} sequence length {sequence_length} is below expected minimum {minimum}"
        )
    elif sequence_length > maximum:
        errors.append(
            f"{chain_type} sequence length {sequence_length} exceeds expected maximum {maximum}"
        )
    if chain_type == "unknown":
        warnings.append("Unknown chain type limits antibody-specific validation.")


def _check_chain_type_plausibility(
    sequence: AntibodySequence,
    normalized: str,
    *,
    warnings: list[str],
) -> None:
    if sequence.chain_type == "paired_heavy_light":
        has_pair_metadata = bool(
            sequence.metadata.get("heavy_sequence_id")
            and sequence.metadata.get("light_sequence_id")
        ) or bool(sequence.metadata.get("paired_chain_sequence_ids"))
        if not has_pair_metadata:
            warnings.append(
                "paired_heavy_light sequence lacks explicit heavy/light pairing metadata"
            )
    if sequence.chain_type in {"light_kappa", "light_lambda"} and len(normalized) > 150:
        warnings.append("Light-chain sequence is unusually long.")
    if sequence.chain_type == "single_domain_vhh" and len(normalized) > 160:
        warnings.append("Single-domain VHH sequence is unusually long.")


def _check_cysteines(sequence: str, *, warnings: list[str]) -> None:
    cysteine_count = sequence.count("C")
    if cysteine_count == 0:
        warnings.append("No cysteine residues detected; antibody domain may be incomplete.")
    if cysteine_count % 2 == 1:
        warnings.append("Odd cysteine count detected; disulfide pairing requires review.")
    if cysteine_count > 8:
        warnings.append("High cysteine count detected; sequence requires expert review.")


def _check_liability_motifs(
    sequence: str,
    *,
    warnings: list[str],
    liability_flags: list[str],
) -> None:
    motif_checks = (
        (GLYCOSYLATION_RE, "potential N-linked glycosylation motif"),
        (DEAMIDATION_RE, "deamidation-prone motif"),
        (OXIDATION_RE, "oxidation-prone residue"),
        (CLIPPING_RE, "clipping or proteolysis-liability motif"),
        (HYDROPHOBIC_RUN_RE, "hydrophobic run liability motif"),
    )
    for pattern, label in motif_checks:
        if pattern.search(sequence):
            liability_flags.append(label)
            warnings.append(f"{label} detected; heuristic review flag only.")


def _check_unusual_cdr_lengths(sequence: AntibodySequence, *, warnings: list[str]) -> None:
    cdr_lengths = sequence.metadata.get("cdr_lengths")
    if not isinstance(cdr_lengths, dict):
        return
    cdr3 = _int_or_none(cdr_lengths.get("cdr3"))
    if cdr3 is not None and (cdr3 < 5 or cdr3 > 30):
        warnings.append("Unusual CDR3 length detected; expert review recommended.")
    for cdr_name in ("cdr1", "cdr2"):
        value = _int_or_none(cdr_lengths.get(cdr_name))
        if value is not None and (value < 3 or value > 20):
            warnings.append(f"Unusual {cdr_name.upper()} length detected.")


def _duplicate_sequence_ids(
    sequence: AntibodySequence,
    normalized: str,
    existing_sequences: Iterable[AntibodySequence] | None,
) -> list[str]:
    if existing_sequences is None:
        return []
    duplicates: list[str] = []
    for existing in existing_sequences:
        if _normalized_sequence(existing.amino_acid_sequence) == normalized:
            duplicates.append(existing.sequence_id)
    return sorted(set(duplicates))


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
    "HEURISTIC_LIMITATION",
    "validate_antibody_sequence",
    "validate_antibody_sequences",
]
