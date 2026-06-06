from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from molecule_ranker.biologics.schemas import AntigenContext

AntigenRecord = Mapping[str, Any]
AntigenSource = Literal[
    "target_record",
    "structure_record",
    "literature_claim",
    "external_registry_metadata",
    "user_supplied_annotation",
]

UNKNOWN_EPITOPE_WARNING = (
    "Epitope context is unknown; antibody generation may only use broad "
    "target-context mode unless source-backed epitope context is added."
)
EPITOPE_SPECIFIC_DISABLED_WARNING = (
    "Epitope-specific antibody design is disabled by default and requires "
    "source-backed epitope context plus an explicit enable flag."
)


def build_antigen_contexts(
    *,
    target_records: Iterable[AntigenRecord] = (),
    structure_records: Iterable[AntigenRecord] = (),
    literature_claims: Iterable[AntigenRecord] = (),
    external_registry_metadata: Iterable[AntigenRecord] = (),
    user_supplied_antigen_annotations: Iterable[AntigenRecord] = (),
) -> list[AntigenContext]:
    contexts_by_target: dict[str, AntigenContext] = {}
    source_batches: tuple[tuple[AntigenSource, Iterable[AntigenRecord]], ...] = (
        ("target_record", target_records),
        ("structure_record", structure_records),
        ("literature_claim", literature_claims),
        ("external_registry_metadata", external_registry_metadata),
        ("user_supplied_annotation", user_supplied_antigen_annotations),
    )
    for source, records in source_batches:
        for record in records:
            context = _context_from_record(record, source=source)
            existing = contexts_by_target.get(context.target_symbol)
            if existing is None:
                contexts_by_target[context.target_symbol] = context
            else:
                _merge_context(existing, context)

    return sorted(contexts_by_target.values(), key=lambda context: context.target_symbol)


def antigen_generation_guardrails(
    context: AntigenContext,
    *,
    enable_epitope_specific_design: bool = False,
) -> dict[str, Any]:
    has_source_backed_epitope = bool(context.epitope_description and context.epitope_source)
    epitope_specific_design_allowed = (
        enable_epitope_specific_design and has_source_backed_epitope
    )
    warnings = list(context.warnings)
    if not has_source_backed_epitope:
        warnings = _append_unique(warnings, [UNKNOWN_EPITOPE_WARNING])
    if not epitope_specific_design_allowed:
        warnings = _append_unique(warnings, [EPITOPE_SPECIFIC_DISABLED_WARNING])

    return {
        "antigen_context_id": context.antigen_context_id,
        "target_symbol": context.target_symbol,
        "generation_context_mode": (
            "epitope_context" if has_source_backed_epitope else "broad_target_context"
        ),
        "broad_target_context_allowed": True,
        "epitope_context_available": has_source_backed_epitope,
        "epitope_specific_design_allowed": epitope_specific_design_allowed,
        "warnings": warnings,
    }


def _context_from_record(
    record: AntigenRecord,
    *,
    source: AntigenSource,
) -> AntigenContext:
    target_symbol = _target_symbol(record)
    epitope_description = _epitope_description(record)
    epitope_source = _epitope_source(record, source=source)
    warnings = _string_list(record.get("warnings"))
    metadata = {
        "antigen_source": source,
        "source_record_id": _source_record_id(record),
        "epitope_status": "unknown",
        "generation_context_mode": "broad_target_context",
        "epitope_specific_design_allowed_by_default": False,
    }

    if epitope_description and not epitope_source:
        warnings.append(
            "Epitope description was ignored because no retrieved, imported, "
            "or user-supplied epitope source was provided."
        )
        epitope_description = None
    elif epitope_description and epitope_source:
        metadata["epitope_status"] = "source_backed"
        metadata["generation_context_mode"] = "epitope_context"
        warnings = _append_unique(warnings, [EPITOPE_SPECIFIC_DISABLED_WARNING])
    else:
        warnings = _append_unique(warnings, [UNKNOWN_EPITOPE_WARNING])

    return AntigenContext(
        antigen_context_id=_context_id(record, target_symbol=target_symbol),
        target_symbol=target_symbol,
        antigen_name=_antigen_name(record, target_symbol),
        antigen_identifiers=_identifiers(record),
        epitope_description=epitope_description,
        epitope_source=epitope_source if epitope_description else None,
        structure_context_ids=_structure_context_ids(record),
        evidence_item_ids=_string_list(
            record.get("evidence_item_ids") or record.get("evidence_ids")
        ),
        confidence=_bounded_confidence(record.get("confidence"), epitope_description),
        warnings=warnings,
        metadata=metadata,
    )


def _merge_context(existing: AntigenContext, incoming: AntigenContext) -> None:
    existing.antigen_identifiers.update(incoming.antigen_identifiers)
    existing.structure_context_ids = _append_unique(
        existing.structure_context_ids,
        incoming.structure_context_ids,
    )
    existing.evidence_item_ids = _append_unique(
        existing.evidence_item_ids,
        incoming.evidence_item_ids,
    )
    existing.warnings = _append_unique(existing.warnings, incoming.warnings)
    existing.confidence = max(existing.confidence, incoming.confidence)
    existing.metadata.setdefault("merged_sources", [])
    if isinstance(existing.metadata["merged_sources"], list):
        existing.metadata["merged_sources"] = _append_unique(
            existing.metadata["merged_sources"],
            [str(incoming.metadata.get("antigen_source", "unknown"))],
        )

    if not existing.epitope_description and incoming.epitope_description:
        existing.epitope_description = incoming.epitope_description
        existing.epitope_source = incoming.epitope_source
        existing.metadata["epitope_status"] = "source_backed"
        existing.metadata["generation_context_mode"] = "epitope_context"
    if existing.antigen_name == existing.target_symbol and incoming.antigen_name:
        existing.antigen_name = incoming.antigen_name


def _target_symbol(record: AntigenRecord) -> str:
    value = _first_string(
        record,
        "target_symbol",
        "target",
        "gene_symbol",
        "symbol",
        default="UNKNOWN",
    )
    normalized = re.sub(r"\s+", "", value).upper()
    return normalized or "UNKNOWN"


def _antigen_name(record: AntigenRecord, target_symbol: str) -> str:
    return _first_string(
        record,
        "antigen_name",
        "target_name",
        "protein_name",
        "name",
        default=target_symbol,
    )


def _epitope_description(record: AntigenRecord) -> str | None:
    return _string_or_none(
        record.get("epitope_description")
        or record.get("epitope")
        or record.get("epitope_region")
    )


def _epitope_source(record: AntigenRecord, *, source: AntigenSource) -> str | None:
    explicit = _string_or_none(
        record.get("epitope_source")
        or record.get("epitope_reference")
        or record.get("source_record_id")
        or record.get("evidence_item_id")
        or record.get("evidence_id")
        or record.get("pmid")
        or record.get("doi")
        or record.get("id")
    )
    if explicit:
        return f"{source}:{explicit}"
    return None


def _identifiers(record: AntigenRecord) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    raw = record.get("antigen_identifiers") or record.get("identifiers")
    if isinstance(raw, Mapping):
        identifiers.update(
            {str(key): str(value) for key, value in raw.items() if value is not None}
        )
    for key, alias in (
        ("uniprot_id", "uniprot"),
        ("ensembl_id", "ensembl"),
        ("ncbi_gene_id", "ncbi_gene"),
        ("target_id", "target_id"),
        ("structure_id", "structure"),
    ):
        value = _string_or_none(record.get(key))
        if value:
            identifiers.setdefault(alias, value)
    return identifiers


def _structure_context_ids(record: AntigenRecord) -> list[str]:
    return _string_list(
        record.get("structure_context_ids")
        or record.get("structure_ids")
        or record.get("structure_id")
        or record.get("pdb_ids")
        or record.get("pdb_id")
    )


def _source_record_id(record: AntigenRecord) -> str | None:
    return _first_string(
        record,
        "source_record_id",
        "evidence_item_id",
        "evidence_id",
        "target_id",
        "structure_id",
        "registry_id",
        "id",
        default="",
    ) or None


def _context_id(record: AntigenRecord, *, target_symbol: str) -> str:
    explicit = _first_string(record, "antigen_context_id", default="")
    if explicit:
        return _slug_id("ag", explicit)
    source_record_id = _source_record_id(record)
    if source_record_id:
        return _slug_id("ag", f"{target_symbol}-{source_record_id}")
    digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, default=str).encode()
    ).hexdigest()[:10]
    return _slug_id("ag", f"{target_symbol}-{digest}")


def _bounded_confidence(value: Any, epitope_description: str | None) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.6 if epitope_description else 0.35
    return round(max(0.0, min(confidence, 1.0)), 3)


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
    record: AntigenRecord,
    *keys: str,
    default: str,
) -> str:
    for key in keys:
        value = _string_or_none(record.get(key))
        if value:
            return value
    return default


def _append_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for value in [*existing, *incoming]:
        if value not in merged:
            merged.append(value)
    return merged


def _slug_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value).strip()).strip("-")
    if not slug:
        digest = hashlib.sha256(str(value).encode()).hexdigest()[:10]
        slug = digest
    return slug if slug.startswith(f"{prefix}-") else f"{prefix}-{slug}"


__all__ = [
    "EPITOPE_SPECIFIC_DISABLED_WARNING",
    "UNKNOWN_EPITOPE_WARNING",
    "AntigenRecord",
    "antigen_generation_guardrails",
    "build_antigen_contexts",
]
