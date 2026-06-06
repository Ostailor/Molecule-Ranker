from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from molecule_ranker.biologics.schemas import (
    AntibodyChainType,
    AntibodySequence,
    AntibodySequenceSource,
    BiologicCandidate,
    BiologicOrigin,
    BiologicType,
)
from molecule_ranker.biologics.scoring import score_biologic_candidate

SourceKind = Literal[
    "chembl",
    "literature",
    "external_registry",
    "imported_registry",
    "user_supplied",
    "antibody_database_plugin",
]
BiologicRecord = Mapping[str, Any]
BiologicAdapter = Callable[..., Iterable[BiologicRecord]]

ANTIBODY_TYPES: set[BiologicType] = {
    "monoclonal_antibody",
    "bispecific_antibody",
    "nanobody",
    "antibody_fragment",
}


@dataclass
class BiologicRetrievalResult:
    candidates: list[BiologicCandidate]
    sequences: list[AntibodySequence] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_files: dict[str, Path] = field(default_factory=dict)


def rank_retrieved_biologics(
    candidates: list[BiologicCandidate],
) -> list[BiologicCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            -score_biologic_candidate(candidate),
            candidate.biologic_id,
        ),
    )


def retrieve_existing_biologics(
    *,
    target_symbols: Iterable[str] = (),
    disease_name: str | None = None,
    chembl_records: Iterable[BiologicRecord] = (),
    literature_evidence: Iterable[BiologicRecord] = (),
    external_registry_records: Iterable[BiologicRecord] = (),
    imported_records: Iterable[BiologicRecord] = (),
    user_candidate_records: Iterable[BiologicRecord] = (),
    antibody_database_adapters: Iterable[BiologicAdapter] = (),
    output_dir: str | Path | None = None,
) -> BiologicRetrievalResult:
    """Retrieve existing biologic candidates from configured/imported sources.

    This function normalizes source-backed records. It does not discover or call
    antibody databases unless explicit adapter callables are supplied, and it
    only creates antibody sequence records when the source/import includes an
    actual amino-acid sequence field.
    """

    query_targets = _normalized_symbols(target_symbols)
    query_disease = _normalized_text(disease_name)
    candidates_by_id: dict[str, BiologicCandidate] = {}
    sequences_by_id: dict[str, AntibodySequence] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    source_batches: list[tuple[SourceKind, Iterable[BiologicRecord]]] = [
        ("chembl", chembl_records),
        ("external_registry", external_registry_records),
        ("imported_registry", imported_records),
        ("user_supplied", user_candidate_records),
    ]
    for adapter_index, adapter in enumerate(antibody_database_adapters, start=1):
        try:
            source_batches.append(
                (
                    "antibody_database_plugin",
                    adapter(
                        target_symbols=sorted(query_targets),
                        disease_name=disease_name,
                    ),
                )
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            warnings.append(
                "Antibody database plugin adapter "
                f"{adapter_index} failed and was skipped: {exc}"
            )

    for source_kind, records in source_batches:
        for record in records:
            if not _record_matches_query(record, query_targets, query_disease):
                continue
            candidate = _candidate_from_record(
                record,
                source_kind=source_kind,
                query_targets=query_targets,
                disease_name=disease_name,
            )
            new_sequences, sequence_warnings = _sequences_from_record(
                record,
                candidate=candidate,
                source_kind=source_kind,
            )
            candidate.sequence_ids = _append_unique(
                candidate.sequence_ids,
                [sequence.sequence_id for sequence in new_sequences],
            )
            candidate.warnings = _append_unique(candidate.warnings, sequence_warnings)
            if _is_sequence_expected(candidate, record) and not new_sequences:
                candidate.warnings = _append_unique(
                    candidate.warnings,
                    [
                        (
                            "Antibody/biologic sequence unavailable from source; "
                            "sequence-specific analysis is unavailable."
                        )
                    ],
                )
            for sequence in new_sequences:
                sequences_by_id[sequence.sequence_id] = sequence
            _merge_candidate(candidates_by_id, candidate)
            for evidence in _evidence_items_from_record(
                record,
                candidate_id=candidate.biologic_id,
                source_kind=source_kind,
            ):
                evidence_by_id[evidence["evidence_item_id"]] = evidence

    for record in literature_evidence:
        if not _record_matches_query(record, query_targets, query_disease):
            continue
        linked_candidate = _linked_candidate(candidates_by_id.values(), record)
        if linked_candidate is None and _has_biologic_identity(record):
            linked_candidate = _candidate_from_record(
                record,
                source_kind="literature",
                query_targets=query_targets,
                disease_name=disease_name,
            )
            linked_candidate.warnings = _append_unique(
                linked_candidate.warnings,
                [
                    (
                        "Literature-derived biologic identity requires review; "
                        "no sequence is inferred from publication evidence."
                    )
                ],
            )
            _merge_candidate(candidates_by_id, linked_candidate)
        if linked_candidate is None:
            warnings.append(
                "Literature evidence was not linked because no biologic candidate "
                "identifier or name was supplied."
            )
            continue
        evidence = _evidence_item_from_literature(record, linked_candidate.biologic_id)
        evidence_by_id[evidence["evidence_item_id"]] = evidence
        linked_candidate.evidence_item_ids = _append_unique(
            linked_candidate.evidence_item_ids,
            [evidence["evidence_item_id"]],
        )
        linked_candidate.direct_experimental_evidence = (
            linked_candidate.direct_experimental_evidence
            or _truthy(record.get("direct_experimental_evidence"))
        )

    candidates = rank_retrieved_biologics(list(candidates_by_id.values()))
    result = BiologicRetrievalResult(
        candidates=candidates,
        sequences=sorted(sequences_by_id.values(), key=lambda sequence: sequence.sequence_id),
        evidence_items=sorted(
            evidence_by_id.values(),
            key=lambda evidence: str(evidence["evidence_item_id"]),
        ),
        warnings=warnings,
    )
    if output_dir is not None:
        result.output_files = write_biologic_retrieval_outputs(result, output_dir)
    return result


def write_biologic_retrieval_outputs(
    result: BiologicRetrievalResult,
    output_dir: str | Path,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    candidates_path = output_path / "biologic_candidates.json"
    evidence_path = output_path / "biologic_evidence.json"

    candidates_payload = {
        "biologic_candidates": [
            candidate.model_dump(mode="json") for candidate in result.candidates
        ],
        "antibody_sequences": [
            sequence.model_dump(mode="json") for sequence in result.sequences
        ],
        "ranked_biologic_ids": [candidate.biologic_id for candidate in result.candidates],
        "warnings": result.warnings,
        "limitations": [
            (
                "Existing antibody candidates may be ranked without sequence when "
                "source evidence exists, but sequence-specific analysis is unavailable."
            ),
            "No antibody sequences are inferred or fabricated by retrieval.",
        ],
    }
    evidence_payload = {
        "evidence_items": result.evidence_items,
        "warnings": result.warnings,
    }

    candidates_path.write_text(
        json.dumps(candidates_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "biologic_candidates": candidates_path,
        "biologic_evidence": evidence_path,
    }


def _candidate_from_record(
    record: BiologicRecord,
    *,
    source_kind: SourceKind,
    query_targets: set[str],
    disease_name: str | None,
) -> BiologicCandidate:
    biologic_id = _biologic_id(record, source_kind)
    evidence_item_ids = _string_list(
        record.get("evidence_item_ids") or record.get("evidence_ids")
    )
    identifiers = _identifiers(record, source_kind)
    source_record_id = _source_record_id(record, source_kind)
    if source_record_id:
        identifiers.setdefault(source_kind, source_record_id)

    target_symbols = _target_symbols(record) or sorted(query_targets)
    antigen_names = _string_list(
        record.get("antigen_names")
        or record.get("antigens")
        or record.get("antigen_name")
        or record.get("target_names")
    )
    candidate = BiologicCandidate(
        biologic_id=biologic_id,
        name=_first_string(
            record,
            "name",
            "pref_name",
            "molecule_name",
            "biologic_name",
            "candidate_name",
            default=biologic_id,
        ),
        biologic_type=_biologic_type(record),
        origin=_origin_for_source(source_kind),
        target_symbols=target_symbols,
        antigen_names=antigen_names,
        disease_name=_string_or_none(record.get("disease_name"))
        or _string_or_none(record.get("disease"))
        or disease_name,
        identifiers=identifiers,
        sequence_ids=_string_list(record.get("sequence_ids")),
        structure_ids=_string_list(
            record.get("structure_ids") or record.get("structure_context_ids")
        ),
        evidence_item_ids=evidence_item_ids,
        direct_experimental_evidence=_truthy(record.get("direct_experimental_evidence")),
        warnings=_string_list(record.get("warnings")),
        metadata={
            "retrieval_source": source_kind,
            "source_record_id": source_record_id,
            "sequence_specific_analysis_available": bool(record.get("sequence_ids")),
        },
    )
    return candidate


def _sequences_from_record(
    record: BiologicRecord,
    *,
    candidate: BiologicCandidate,
    source_kind: SourceKind,
) -> tuple[list[AntibodySequence], list[str]]:
    sequence_specs = _sequence_specs(record)
    sequences: list[AntibodySequence] = []
    warnings: list[str] = []
    for index, spec in enumerate(sequence_specs, start=1):
        raw_sequence = spec["sequence"]
        normalized_sequence = re.sub(r"\s+", "", str(raw_sequence)).upper()
        sequence_id = _sequence_id(
            record,
            candidate=candidate,
            chain_type=spec["chain_type"],
            sequence=normalized_sequence,
            index=index,
        )
        try:
            sequences.append(
                AntibodySequence(
                    sequence_id=sequence_id,
                    biologic_id=candidate.biologic_id,
                    chain_type=spec["chain_type"],
                    amino_acid_sequence=normalized_sequence,
                    sequence_length=len(normalized_sequence),
                    species_origin=_string_or_none(
                        record.get("species_origin") or record.get("species")
                    ),
                    is_generated=False,
                    parent_sequence_ids=[],
                    source=_sequence_source(source_kind),
                    source_record_id=_source_record_id(record, source_kind),
                    created_at=datetime.now(UTC),
                    metadata={
                        "retrieval_source": source_kind,
                        "source_sequence_field": spec["field"],
                    },
                )
            )
        except ValueError as exc:
            warnings.append(
                f"Sequence field {spec['field']!r} was not imported for "
                f"{candidate.biologic_id}: {exc}"
            )
    return sequences, warnings


def _sequence_specs(record: BiologicRecord) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for field_name, chain_type in (
        ("heavy_chain", "heavy"),
        ("heavy_chain_sequence", "heavy"),
        ("light_chain", "light_kappa"),
        ("light_chain_sequence", "light_kappa"),
        ("kappa_chain_sequence", "light_kappa"),
        ("lambda_chain_sequence", "light_lambda"),
        ("vhh_sequence", "single_domain_vhh"),
        ("scfv_sequence", "scfv"),
        ("amino_acid_sequence", None),
        ("sequence", None),
    ):
        value = record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        specs.append(
            {
                "field": field_name,
                "sequence": value,
                "chain_type": chain_type or _chain_type(record),
            }
        )
    for item in _list_of_mappings(record.get("sequences")):
        value = item.get("amino_acid_sequence") or item.get("sequence")
        if not isinstance(value, str) or not value.strip():
            continue
        specs.append(
            {
                "field": "sequences",
                "sequence": value,
                "chain_type": _chain_type(item),
            }
        )
    return specs


def _evidence_items_from_record(
    record: BiologicRecord,
    *,
    candidate_id: str,
    source_kind: SourceKind,
) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for evidence in _list_of_mappings(record.get("evidence") or record.get("evidence_items")):
        evidence_id = _evidence_id(evidence, candidate_id=candidate_id, source_kind=source_kind)
        evidence_items.append(
            {
                "evidence_item_id": evidence_id,
                "biologic_id": candidate_id,
                "source": source_kind,
                "source_record_id": _string_or_none(
                    evidence.get("source_record_id") or evidence.get("id")
                )
                or _source_record_id(record, source_kind),
                "target_symbols": _target_symbols(evidence) or _target_symbols(record),
                "disease_name": _string_or_none(evidence.get("disease_name"))
                or _string_or_none(record.get("disease_name"))
                or _string_or_none(record.get("disease")),
                "direct_experimental_evidence": _truthy(
                    evidence.get("direct_experimental_evidence")
                    or record.get("direct_experimental_evidence")
                ),
                "metadata": _metadata_without_sequence(evidence),
            }
        )
    for evidence_id in _string_list(
        record.get("evidence_item_ids") or record.get("evidence_ids")
    ):
        evidence_items.append(
            {
                "evidence_item_id": evidence_id,
                "biologic_id": candidate_id,
                "source": source_kind,
                "source_record_id": _source_record_id(record, source_kind),
                "target_symbols": _target_symbols(record),
                "disease_name": _string_or_none(record.get("disease_name"))
                or _string_or_none(record.get("disease")),
                "direct_experimental_evidence": _truthy(
                    record.get("direct_experimental_evidence")
                ),
                "metadata": {"preserved_source_evidence_id": evidence_id},
            }
        )
    return evidence_items


def _evidence_item_from_literature(
    record: BiologicRecord,
    candidate_id: str,
) -> dict[str, Any]:
    evidence_id = _evidence_id(record, candidate_id=candidate_id, source_kind="literature")
    return {
        "evidence_item_id": evidence_id,
        "biologic_id": candidate_id,
        "source": "literature",
        "source_record_id": _first_string(
            record,
            "evidence_item_id",
            "evidence_id",
            "pmid",
            "doi",
            "source_record_id",
            "id",
            default=evidence_id,
        ),
        "target_symbols": _target_symbols(record),
        "disease_name": _string_or_none(record.get("disease_name"))
        or _string_or_none(record.get("disease")),
        "direct_experimental_evidence": _truthy(record.get("direct_experimental_evidence")),
        "metadata": _metadata_without_sequence(record),
    }


def _merge_candidate(
    candidates_by_id: dict[str, BiologicCandidate],
    candidate: BiologicCandidate,
) -> None:
    existing = candidates_by_id.get(candidate.biologic_id)
    if existing is None:
        candidates_by_id[candidate.biologic_id] = candidate
        return
    existing.target_symbols = _append_unique(existing.target_symbols, candidate.target_symbols)
    existing.antigen_names = _append_unique(existing.antigen_names, candidate.antigen_names)
    existing.sequence_ids = _append_unique(existing.sequence_ids, candidate.sequence_ids)
    existing.structure_ids = _append_unique(existing.structure_ids, candidate.structure_ids)
    existing.evidence_item_ids = _append_unique(
        existing.evidence_item_ids,
        candidate.evidence_item_ids,
    )
    existing.warnings = _append_unique(existing.warnings, candidate.warnings)
    existing.direct_experimental_evidence = (
        existing.direct_experimental_evidence or candidate.direct_experimental_evidence
    )
    existing.identifiers.update(candidate.identifiers)
    existing.metadata.update(candidate.metadata)


def _biologic_id(record: BiologicRecord, source_kind: SourceKind) -> str:
    explicit = _first_string(
        record,
        "biologic_id",
        "candidate_id",
        "molecule_chembl_id",
        "chembl_id",
        "registry_id",
        "source_record_id",
        "id",
        default="",
    )
    if explicit:
        return _slug_id("bio", explicit)
    digest = hashlib.sha256(
        json.dumps(_metadata_without_sequence(record), sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return f"bio-{source_kind}-{digest}"


def _sequence_id(
    record: BiologicRecord,
    *,
    candidate: BiologicCandidate,
    chain_type: AntibodyChainType,
    sequence: str,
    index: int,
) -> str:
    explicit = _first_string(record, "sequence_id", default="")
    if explicit and index == 1:
        return _slug_id("seq", explicit)
    digest = hashlib.sha256(sequence.encode()).hexdigest()[:10]
    return _slug_id("seq", f"{candidate.biologic_id}-{chain_type}-{digest}-{index}")


def _evidence_id(
    record: BiologicRecord,
    *,
    candidate_id: str,
    source_kind: SourceKind,
) -> str:
    explicit = _first_string(
        record,
        "evidence_item_id",
        "evidence_id",
        "pmid",
        "doi",
        "source_record_id",
        "id",
        default="",
    )
    if explicit:
        return _slug_id("ev", explicit)
    digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return _slug_id("ev", f"{source_kind}-{candidate_id}-{digest}")


def _identifiers(record: BiologicRecord, source_kind: SourceKind) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    raw_identifiers = record.get("identifiers")
    if isinstance(raw_identifiers, Mapping):
        identifiers.update(
            {
                str(key): str(value)
                for key, value in raw_identifiers.items()
                if value is not None
            }
        )
    for key, alias in (
        ("molecule_chembl_id", "chembl"),
        ("chembl_id", "chembl"),
        ("registry_id", "registry"),
        ("benchling_id", "benchling"),
        ("uniprot_id", "uniprot"),
        ("drugbank_id", "drugbank"),
        ("source_record_id", source_kind),
    ):
        value = _string_or_none(record.get(key))
        if value:
            identifiers.setdefault(alias, value)
    return identifiers


def _biologic_type(record: BiologicRecord) -> BiologicType:
    value = " ".join(
        filter(
            None,
            [
                _string_or_none(record.get("biologic_type")),
                _string_or_none(record.get("molecule_type")),
                _string_or_none(record.get("therapeutic_type")),
                _string_or_none(record.get("modality")),
                _string_or_none(record.get("type")),
            ],
        )
    ).lower()
    if "bispecific" in value:
        return "bispecific_antibody"
    if "nanobody" in value or "vhh" in value:
        return "nanobody"
    if "fragment" in value or " fab" in f" {value}" or "scfv" in value:
        return "antibody_fragment"
    if "monoclonal" in value or "antibody" in value or "mab" in value:
        return "monoclonal_antibody"
    if "protein binder" in value or "protein_binder" in value:
        return "protein_binder"
    if "cytokine" in value:
        return "cytokine"
    if "receptor fusion" in value or "fc fusion" in value or "fusion protein" in value:
        return "receptor_fusion"
    if "peptide" in value:
        return "peptide"
    return "other"


def _origin_for_source(source_kind: SourceKind) -> BiologicOrigin:
    if source_kind in {"external_registry", "antibody_database_plugin"}:
        return "external"
    return "existing"


def _sequence_source(source_kind: SourceKind) -> AntibodySequenceSource:
    if source_kind in {"chembl", "antibody_database_plugin"}:
        return "public_database"
    if source_kind == "external_registry":
        return "external_registry"
    if source_kind == "user_supplied":
        return "user_supplied"
    return "imported"


def _chain_type(record: BiologicRecord) -> AntibodyChainType:
    raw = _first_string(record, "chain_type", "chain", default="").lower()
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


def _record_matches_query(
    record: BiologicRecord,
    query_targets: set[str],
    query_disease: str | None,
) -> bool:
    if not query_targets and not query_disease:
        return True
    record_targets = set(_target_symbols(record))
    target_match = bool(query_targets and record_targets & query_targets)
    disease = _normalized_text(record.get("disease_name") or record.get("disease"))
    disease_match = bool(
        query_disease
        and disease
        and (query_disease in disease or disease in query_disease)
    )
    return target_match or disease_match


def _target_symbols(record: BiologicRecord) -> list[str]:
    return _normalized_symbol_list(
        record.get("target_symbols")
        or record.get("targets")
        or record.get("target_symbol")
        or record.get("gene_symbols")
    )


def _linked_candidate(
    candidates: Iterable[BiologicCandidate],
    record: BiologicRecord,
) -> BiologicCandidate | None:
    candidate_ids = {
        _slug_id("bio", value)
        for value in _string_list(
            record.get("biologic_id")
            or record.get("candidate_id")
            or record.get("molecule_chembl_id")
            or record.get("chembl_id")
            or record.get("registry_id")
        )
    }
    names = {
        _normalized_text(value)
        for value in _string_list(
            record.get("biologic_name")
            or record.get("candidate_name")
            or record.get("name")
            or record.get("molecule_name")
        )
    }
    for candidate in candidates:
        if candidate.biologic_id in candidate_ids:
            return candidate
        if _normalized_text(candidate.name) in names:
            return candidate
    return None


def _has_biologic_identity(record: BiologicRecord) -> bool:
    return bool(
        _first_string(
            record,
            "biologic_id",
            "candidate_id",
            "molecule_chembl_id",
            "chembl_id",
            "registry_id",
            "name",
            "molecule_name",
            "biologic_name",
            "candidate_name",
            default="",
        )
    )


def _is_sequence_expected(candidate: BiologicCandidate, record: BiologicRecord) -> bool:
    if candidate.biologic_type in ANTIBODY_TYPES:
        return True
    return any(
        record.get(key)
        for key in (
            "sequence_expected",
            "has_sequence",
            "amino_acid_sequence_available",
        )
    )


def _source_record_id(record: BiologicRecord, source_kind: SourceKind) -> str | None:
    if source_kind == "chembl":
        return _first_string(record, "molecule_chembl_id", "chembl_id", default="") or None
    return _first_string(
        record,
        "source_record_id",
        "registry_id",
        "benchling_id",
        "id",
        default="",
    ) or None


def _metadata_without_sequence(record: BiologicRecord) -> dict[str, Any]:
    sequence_keys = {
        "amino_acid_sequence",
        "sequence",
        "sequences",
        "heavy_chain",
        "heavy_chain_sequence",
        "light_chain",
        "light_chain_sequence",
        "kappa_chain_sequence",
        "lambda_chain_sequence",
        "vhh_sequence",
        "scfv_sequence",
    }
    return {str(key): value for key, value in record.items() if key not in sequence_keys}


def _normalized_symbols(values: Iterable[str]) -> set[str]:
    return set(_normalized_symbol_list(list(values)))


def _normalized_symbol_list(value: Any) -> list[str]:
    symbols = _string_list(value)
    return sorted(
        {
            re.sub(r"\s+", "", symbol).upper()
            for symbol in symbols
            if re.sub(r"\s+", "", symbol)
        }
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)]


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_string(
    record: BiologicRecord,
    *keys: str,
    default: str,
) -> str:
    for key in keys:
        value = _string_or_none(record.get(key))
        if value:
            return value
    return default


def _list_of_mappings(value: Any) -> list[BiologicRecord]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _append_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for value in [*existing, *incoming]:
        if value not in merged:
            merged.append(value)
    return merged


def _normalized_text(value: Any) -> str | None:
    text = _string_or_none(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().lower()


def _slug_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value).strip()).strip("-")
    if not slug:
        digest = hashlib.sha256(str(value).encode()).hexdigest()[:12]
        slug = digest
    return slug if slug.startswith(f"{prefix}-") else f"{prefix}-{slug}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "direct"}
    return bool(value)


__all__ = [
    "BiologicAdapter",
    "BiologicRecord",
    "BiologicRetrievalResult",
    "rank_retrieved_biologics",
    "retrieve_existing_biologics",
    "write_biologic_retrieval_outputs",
]
