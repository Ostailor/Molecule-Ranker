from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from molecule_ranker.biologics.schemas import (
    AntibodyChainType,
    AntibodyNoveltyAssessment,
    AntibodyNoveltyClass,
    AntibodySequence,
)

NoveltyRecordSource = Literal[
    "known_sequences",
    "internal_candidate_registry",
    "imported_external_registry",
    "generated_sequence_archive",
    "parent_sequences",
    "public_antibody_database_plugin",
]
NoveltyRecordInput = AntibodySequence | Mapping[str, Any]
PublicAntibodyDatabaseAdapter = Callable[..., Iterable[NoveltyRecordInput]]

SOURCE_LIMITATION_WARNING = (
    "Antibody novelty checks are limited to the supplied/configured sources "
    "checked and do not establish global novelty."
)
GENERATED_EXACT_DUPLICATE_REJECTION = (
    "Generated antibody exact sequence duplicate was rejected by default."
)
NEAR_DUPLICATE_REVIEW_WARNING = "Near-duplicate antibody sequence requires expert review."


@dataclass(frozen=True)
class _KnownAntibodyRecord:
    record_id: str
    sequence: str
    source: NoveltyRecordSource
    chain_type: AntibodyChainType = "unknown"
    cdr3: str | None = None
    is_generated: bool = False


def assess_antibody_novelty(
    *,
    novelty_id: str,
    biologic_id: str,
    sequences: list[AntibodySequence],
    known_sequences: Mapping[str, str] | None = None,
    sources_checked: list[str] | None = None,
    internal_candidate_registry: Iterable[NoveltyRecordInput] = (),
    imported_external_registry: Iterable[NoveltyRecordInput] = (),
    generated_sequence_archive: Iterable[NoveltyRecordInput] = (),
    parent_sequences: Iterable[NoveltyRecordInput] | Mapping[str, str] = (),
    public_antibody_database_adapters: Iterable[PublicAntibodyDatabaseAdapter] = (),
    reject_generated_exact_duplicates: bool = True,
) -> AntibodyNoveltyAssessment:
    records, adapter_warnings = _collect_records(
        known_sequences=known_sequences or {},
        internal_candidate_registry=internal_candidate_registry,
        imported_external_registry=imported_external_registry,
        generated_sequence_archive=generated_sequence_archive,
        parent_sequences=parent_sequences,
        public_antibody_database_adapters=public_antibody_database_adapters,
        query_sequences=sequences,
    )
    observed_sources = _sources_checked(sources_checked or [], records)
    warnings = [SOURCE_LIMITATION_WARNING, *adapter_warnings]

    query_full_sequences = [
        _normalized_sequence(sequence.amino_acid_sequence) for sequence in sequences
    ]
    query_cdr3s = [_cdr3_from_sequence(sequence) for sequence in sequences]
    query_cdr3s = [cdr3 for cdr3 in query_cdr3s if cdr3]
    query_parent_ids = {
        parent_id
        for sequence in sequences
        for parent_id in sequence.parent_sequence_ids
    }

    exact_matches = _exact_full_sequence_matches(query_full_sequences, records)
    heavy_matches = _chain_exact_matches(query_full_sequences, records, chain_type="heavy")
    light_matches = _light_chain_exact_matches(query_full_sequences, records)
    cdr3_matches = _cdr3_exact_matches(query_cdr3s, records)
    nearest_sequence = _nearest_sequence_identity(query_full_sequences, records)
    nearest_cdr3 = _nearest_cdr3_identity(query_cdr3s, records)
    parent_similarity = _parent_similarity(query_full_sequences, query_parent_ids, records)

    exact_sequence_match = bool(exact_matches)
    cdr3_exact_match = bool(cdr3_matches) if query_cdr3s else None
    generated_exact_duplicate_rejected = bool(
        reject_generated_exact_duplicates
        and exact_sequence_match
        and any(sequence.is_generated for sequence in sequences)
    )
    if generated_exact_duplicate_rejected:
        warnings.append(GENERATED_EXACT_DUPLICATE_REJECTION)

    novelty_class = _novelty_class(
        exact_sequence_match=exact_sequence_match,
        nearest_sequence_identity=nearest_sequence[0],
        cdr3_exact_match=cdr3_exact_match,
        nearest_cdr3_identity=nearest_cdr3[0],
        parent_similarity=parent_similarity[0],
        sources_available=bool(records),
    )
    review_required = novelty_class in {"near_duplicate", "close_variant"} or bool(cdr3_matches)
    if review_required:
        warnings.append(NEAR_DUPLICATE_REVIEW_WARNING)

    return AntibodyNoveltyAssessment(
        novelty_id=novelty_id,
        biologic_id=biologic_id,
        sequence_ids=[sequence.sequence_id for sequence in sequences],
        exact_sequence_match=exact_sequence_match,
        nearest_sequence_identity=nearest_sequence[0],
        nearest_known_record=nearest_sequence[1] or (exact_matches[0] if exact_matches else None),
        cdr3_exact_match=cdr3_exact_match,
        cdr3_nearest_identity=nearest_cdr3[0],
        novelty_class=novelty_class,
        sources_checked=observed_sources,
        warnings=sorted(set(warnings)),
        metadata={
            "global_novelty_claimed": False,
            "exact_full_sequence_duplicate_record_ids": exact_matches,
            "heavy_chain_duplicate_record_ids": heavy_matches,
            "light_chain_duplicate_record_ids": light_matches,
            "cdr3_exact_duplicate_record_ids": cdr3_matches,
            "nearest_cdr3_record": nearest_cdr3[1],
            "parent_sequence_similarity": parent_similarity[0],
            "nearest_parent_record": parent_similarity[1],
            "generated_exact_duplicate_rejected": generated_exact_duplicate_rejected,
            "generated_vs_existing_lineage": _generated_vs_existing_lineage(
                sequences,
                records,
                exact_matches=exact_matches,
                nearest_record_id=nearest_sequence[1],
            ),
            "review_required": review_required,
            "records_compared": len(records),
        },
    )


def _collect_records(
    *,
    known_sequences: Mapping[str, str],
    internal_candidate_registry: Iterable[NoveltyRecordInput],
    imported_external_registry: Iterable[NoveltyRecordInput],
    generated_sequence_archive: Iterable[NoveltyRecordInput],
    parent_sequences: Iterable[NoveltyRecordInput] | Mapping[str, str],
    public_antibody_database_adapters: Iterable[PublicAntibodyDatabaseAdapter],
    query_sequences: list[AntibodySequence],
) -> tuple[list[_KnownAntibodyRecord], list[str]]:
    records: list[_KnownAntibodyRecord] = [
        _record_from_mapping(
            {"record_id": record_id, "sequence": sequence},
            source="known_sequences",
        )
        for record_id, sequence in known_sequences.items()
    ]
    source_batches: tuple[tuple[NoveltyRecordSource, Iterable[NoveltyRecordInput]], ...] = (
        ("internal_candidate_registry", internal_candidate_registry),
        ("imported_external_registry", imported_external_registry),
        ("generated_sequence_archive", generated_sequence_archive),
    )
    for source, values in source_batches:
        records.extend(_records_from_iterable(values, source=source))

    if isinstance(parent_sequences, Mapping):
        records.extend(
            _record_from_mapping(
                {"record_id": record_id, "sequence": sequence},
                source="parent_sequences",
            )
            for record_id, sequence in parent_sequences.items()
        )
    else:
        records.extend(_records_from_iterable(parent_sequences, source="parent_sequences"))

    warnings: list[str] = []
    for adapter_index, adapter in enumerate(public_antibody_database_adapters, start=1):
        try:
            records.extend(
                _records_from_iterable(
                    adapter(
                        sequence_ids=[sequence.sequence_id for sequence in query_sequences],
                        sequences=[
                            sequence.amino_acid_sequence for sequence in query_sequences
                        ],
                    ),
                    source="public_antibody_database_plugin",
                )
            )
        except Exception as exc:  # pragma: no cover - defensive plugin boundary
            warnings.append(
                "Public antibody database plugin adapter "
                f"{adapter_index} failed and was skipped: {exc}"
            )
    return records, warnings


def _records_from_iterable(
    values: Iterable[NoveltyRecordInput],
    *,
    source: NoveltyRecordSource,
) -> list[_KnownAntibodyRecord]:
    records: list[_KnownAntibodyRecord] = []
    for index, value in enumerate(values, start=1):
        if isinstance(value, AntibodySequence):
            records.append(_record_from_sequence(value, source=source))
        elif isinstance(value, Mapping):
            records.append(_record_from_mapping(value, source=source, index=index))
    return records


def _record_from_sequence(
    sequence: AntibodySequence,
    *,
    source: NoveltyRecordSource,
) -> _KnownAntibodyRecord:
    return _KnownAntibodyRecord(
        record_id=sequence.source_record_id or sequence.sequence_id,
        sequence=_normalized_sequence(sequence.amino_acid_sequence),
        source=source,
        chain_type=sequence.chain_type,
        cdr3=_cdr3_from_sequence(sequence),
        is_generated=sequence.is_generated,
    )


def _record_from_mapping(
    value: Mapping[str, Any],
    *,
    source: NoveltyRecordSource,
    index: int = 1,
) -> _KnownAntibodyRecord:
    sequence = _normalized_sequence(
        str(
            value.get("amino_acid_sequence")
            or value.get("sequence")
            or value.get("heavy_chain")
            or value.get("light_chain")
            or ""
        )
    )
    record_id = str(
        value.get("record_id")
        or value.get("sequence_id")
        or value.get("source_record_id")
        or value.get("id")
        or f"{source}-{index}"
    )
    return _KnownAntibodyRecord(
        record_id=record_id,
        sequence=sequence,
        source=source,
        chain_type=_chain_type(value),
        cdr3=_normalized_optional_string(
            value.get("cdr3")
            or value.get("cdrh3")
            or _mapping_cdr3(value.get("cdr_sequences"))
        ),
        is_generated=_truthy(value.get("is_generated")) or source == "generated_sequence_archive",
    )


def _sources_checked(
    requested_sources: list[str],
    records: list[_KnownAntibodyRecord],
) -> list[str]:
    observed = [record.source for record in records]
    merged: list[str] = []
    for source in [*requested_sources, *observed]:
        if source not in merged:
            merged.append(source)
    return merged


def _exact_full_sequence_matches(
    query_sequences: list[str],
    records: list[_KnownAntibodyRecord],
) -> list[str]:
    return sorted(
        {
            record.record_id
            for query in query_sequences
            for record in records
            if query and record.sequence and query == record.sequence
        }
    )


def _chain_exact_matches(
    query_sequences: list[str],
    records: list[_KnownAntibodyRecord],
    *,
    chain_type: AntibodyChainType,
) -> list[str]:
    return sorted(
        {
            record.record_id
            for query in query_sequences
            for record in records
            if record.chain_type == chain_type and query and query == record.sequence
        }
    )


def _light_chain_exact_matches(
    query_sequences: list[str],
    records: list[_KnownAntibodyRecord],
) -> list[str]:
    return sorted(
        {
            record.record_id
            for query in query_sequences
            for record in records
            if record.chain_type in {"light_kappa", "light_lambda"}
            and query
            and query == record.sequence
        }
    )


def _cdr3_exact_matches(
    query_cdr3s: list[str],
    records: list[_KnownAntibodyRecord],
) -> list[str]:
    return sorted(
        {
            record.record_id
            for query in query_cdr3s
            for record in records
            if query and record.cdr3 and query == record.cdr3
        }
    )


def _nearest_sequence_identity(
    query_sequences: list[str],
    records: list[_KnownAntibodyRecord],
) -> tuple[float | None, str | None]:
    return _nearest_identity(
        query_sequences,
        [(record.sequence, record.record_id) for record in records],
    )


def _nearest_cdr3_identity(
    query_cdr3s: list[str],
    records: list[_KnownAntibodyRecord],
) -> tuple[float | None, str | None]:
    return _nearest_identity(
        query_cdr3s,
        [
            (record.cdr3, record.record_id)
            for record in records
            if record.cdr3 is not None
        ],
    )


def _parent_similarity(
    query_sequences: list[str],
    query_parent_ids: set[str],
    records: list[_KnownAntibodyRecord],
) -> tuple[float | None, str | None]:
    parent_records = [
        record
        for record in records
        if record.source == "parent_sequences" or record.record_id in query_parent_ids
    ]
    return _nearest_identity(
        query_sequences,
        [(record.sequence, record.record_id) for record in parent_records],
    )


def _nearest_identity(
    queries: list[str],
    references: list[tuple[str | None, str]],
) -> tuple[float | None, str | None]:
    best_identity: float | None = None
    best_record: str | None = None
    for query in queries:
        if not query:
            continue
        for reference, record_id in references:
            if not reference:
                continue
            identity = _sequence_identity(query, reference)
            if best_identity is None or identity > best_identity:
                best_identity = identity
                best_record = record_id
    if best_identity is None:
        return None, None
    return round(best_identity, 3), best_record


def _sequence_identity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    denominator = max(len(left), len(right))
    matches = sum(
        1
        for left_residue, right_residue in zip(left, right, strict=False)
        if left_residue == right_residue
    )
    return matches / denominator


def _novelty_class(
    *,
    exact_sequence_match: bool,
    nearest_sequence_identity: float | None,
    cdr3_exact_match: bool | None,
    nearest_cdr3_identity: float | None,
    parent_similarity: float | None,
    sources_available: bool,
) -> AntibodyNoveltyClass:
    if not sources_available:
        return "unknown"
    if exact_sequence_match:
        return "known"
    nearest = max(
        value
        for value in [
            nearest_sequence_identity or 0.0,
            nearest_cdr3_identity or 0.0,
            parent_similarity or 0.0,
        ]
    )
    if cdr3_exact_match or nearest >= 0.98:
        return "near_duplicate"
    if nearest >= 0.85:
        return "close_variant"
    return "novel_candidate"


def _generated_vs_existing_lineage(
    sequences: list[AntibodySequence],
    records: list[_KnownAntibodyRecord],
    *,
    exact_matches: list[str],
    nearest_record_id: str | None,
) -> dict[str, Any]:
    generated = any(sequence.is_generated for sequence in sequences)
    if not generated:
        return {"generated_query": False}
    exact_existing_records = [
        record.record_id
        for record in records
        if record.record_id in exact_matches and not record.is_generated
    ]
    nearest_record = next(
        (record for record in records if record.record_id == nearest_record_id),
        None,
    )
    return {
        "generated_query": True,
        "exact_existing_duplicate_record_ids": exact_existing_records,
        "nearest_record_id": nearest_record_id,
        "nearest_record_source": nearest_record.source if nearest_record else None,
        "nearest_record_generated": nearest_record.is_generated if nearest_record else None,
    }


def _cdr3_from_sequence(sequence: AntibodySequence) -> str | None:
    cdr_sequences = sequence.metadata.get("cdr_sequences")
    cdr3 = _mapping_cdr3(cdr_sequences)
    return _normalized_optional_string(cdr3 or sequence.metadata.get("cdr3"))


def _mapping_cdr3(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("cdr3") or value.get("cdrh3")
    return None


def _chain_type(value: Mapping[str, Any]) -> AntibodyChainType:
    raw = str(value.get("chain_type") or value.get("chain") or "unknown").lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in {
        "heavy",
        "light_kappa",
        "light_lambda",
        "paired_heavy_light",
        "single_domain_vhh",
        "scfv",
        "unknown",
    }:
        return cast(AntibodyChainType, normalized)
    if "lambda" in normalized:
        return "light_lambda"
    if "kappa" in normalized or normalized == "light":
        return "light_kappa"
    if "heavy" in normalized:
        return "heavy"
    if "vhh" in normalized:
        return "single_domain_vhh"
    if "scfv" in normalized:
        return "scfv"
    return "unknown"


def _normalized_sequence(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _normalized_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = _normalized_sequence(str(value))
    return normalized or None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


__all__ = [
    "GENERATED_EXACT_DUPLICATE_REJECTION",
    "NEAR_DUPLICATE_REVIEW_WARNING",
    "SOURCE_LIMITATION_WARNING",
    "PublicAntibodyDatabaseAdapter",
    "assess_antibody_novelty",
]
