from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from molecule_ranker.integrations.schemas import (
    EntityMapping,
    ExternalIdMapping,
    ExternalRecordEnvelope,
    ExternalRecordRef,
    IntegrationAuditEvent,
    MappingSuggestionReport,
    MappingSuggestionRequest,
)

HIGH_CONFIDENCE_SIGNALS = {
    "exact_id",
    "inchi_key",
    "canonical_smiles",
    "registry_id",
    "chembl_id",
    "pubchem_cid",
    "generated_id",
    "user_confirmed",
}
IDENTIFIER_SIGNALS = {
    "exact_id",
    "inchi_key",
    "canonical_smiles",
    "registry_id",
    "chembl_id",
    "pubchem_cid",
    "generated_id",
}
NAME_SIGNALS = {"name_exact"}
CONFLICT_TYPES = {
    "one_internal_maps_to_multiple_external",
    "multiple_internal_map_to_one_external",
    "identifier_mismatch",
    "stale_external_version",
    "schema_mismatch",
    "ambiguous_name_match",
}


class MappingAuditSink(Protocol):
    def write_audit(self, event: IntegrationAuditEvent) -> None: ...


def map_candidate_to_registry_entry(
    internal_candidate: dict[str, Any],
    external_records: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    existing_mappings: list[EntityMapping] | None = None,
    created_by: str | None = None,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    return _resolve_mapping(
        internal_entity=internal_candidate,
        internal_entity_type="candidate",
        internal_entity_id=_required_internal_id(internal_candidate, "candidate_id"),
        external_record_type="registry_entry",
        external_records=external_records,
        project_id=project_id,
        existing_mappings=existing_mappings or [],
        created_by=created_by,
        audit_sink=audit_sink,
    )


def map_assay_result_to_external(
    assay_result: dict[str, Any],
    external_records: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    existing_mappings: list[EntityMapping] | None = None,
    created_by: str | None = None,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    return _resolve_mapping(
        internal_entity=assay_result,
        internal_entity_type="assay_result",
        internal_entity_id=_required_internal_id(assay_result, "result_id"),
        external_record_type="assay_result",
        external_records=external_records,
        project_id=project_id,
        existing_mappings=existing_mappings or [],
        created_by=created_by,
        audit_sink=audit_sink,
    )


def map_review_item_to_external(
    review_item: dict[str, Any],
    external_records: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    existing_mappings: list[EntityMapping] | None = None,
    created_by: str | None = None,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    return _resolve_mapping(
        internal_entity=review_item,
        internal_entity_type="review_item",
        internal_entity_id=_required_internal_id(review_item, "review_item_id"),
        external_record_type="workflow_task",
        external_records=external_records,
        project_id=project_id,
        existing_mappings=existing_mappings or [],
        created_by=created_by,
        audit_sink=audit_sink,
    )


def map_generated_molecule_to_external(
    generated_molecule: dict[str, Any],
    external_records: list[dict[str, Any]],
    *,
    explicitly_exported: bool,
    project_id: str | None = None,
    existing_mappings: list[EntityMapping] | None = None,
    created_by: str | None = None,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    if not explicitly_exported:
        return _rejected_mapping(
            internal_entity_type="generated_molecule",
            internal_entity_id=_required_internal_id(generated_molecule, "generated_id"),
            project_id=project_id,
            external_ref=_placeholder_ref(
                external_system_id=str(generated_molecule.get("external_system_id") or "external"),
                record_type="proposed_compound",
                record_id=str(generated_molecule.get("generated_id")),
            ),
            method="manual",
            confidence=0.0,
            conflict_type="generated_molecule_not_explicitly_exported",
            created_by=created_by,
            audit_sink=audit_sink,
        )
    return _resolve_mapping(
        internal_entity=generated_molecule,
        internal_entity_type="generated_molecule",
        internal_entity_id=_required_internal_id(generated_molecule, "generated_id"),
        external_record_type="proposed_compound",
        external_records=external_records,
        project_id=project_id,
        existing_mappings=existing_mappings or [],
        created_by=created_by,
        audit_sink=audit_sink,
    )


def codex_suggest_mapping(
    *,
    internal_entity_type: str,
    internal_entity_id: str,
    external_ref: ExternalRecordRef,
    project_id: str | None = None,
    confidence: float = 0.5,
    metadata: dict[str, Any] | None = None,
    created_by: str | None = None,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    mapping = EntityMapping(
        mapping_id=f"map-{uuid4().hex[:16]}",
        project_id=project_id,
        internal_entity_type=internal_entity_type,  # type: ignore[arg-type]
        internal_entity_id=internal_entity_id,
        external_ref=external_ref,
        mapping_method="codex_suggested_pending_validation",
        mapping_confidence=max(0.0, min(confidence, 1.0)),
        status="pending_review",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by=created_by,
        metadata={
            "codex_suggestion": True,
            "requires_deterministic_validation": True,
            **(metadata or {}),
        },
    )
    _audit_mapping(mapping, audit_sink, summary="Created Codex-suggested pending mapping.")
    return mapping


def confirm_codex_mapping(
    suggestion: EntityMapping,
    *,
    deterministic_validation: bool,
    audit_sink: MappingAuditSink | None = None,
) -> EntityMapping:
    if suggestion.mapping_method != "codex_suggested_pending_validation":
        raise ValueError("Only Codex-suggested mappings can be confirmed through this path.")
    if not deterministic_validation:
        return suggestion.model_copy(
            update={
                "status": "pending_review",
                "metadata": {
                    **suggestion.metadata,
                    "deterministic_validation": False,
                    "confirmation_blocked": True,
                },
            }
        )
    confirmed = suggestion.model_copy(
        update={
            "mapping_method": "user_confirmed",
            "status": "active",
            "mapping_confidence": max(suggestion.mapping_confidence, 0.95),
            "updated_at": datetime.now(UTC),
            "metadata": {
                **suggestion.metadata,
                "deterministic_validation": True,
            },
        }
    )
    _audit_mapping(confirmed, audit_sink, summary="Confirmed Codex-suggested mapping.")
    return confirmed


def validate_mapping_suggestions(request: MappingSuggestionRequest) -> MappingSuggestionReport:
    observed = _observed_identity_pairs(request.observed_records)
    accepted: list[ExternalIdMapping] = []
    rejected: list[dict[str, object]] = []
    for suggestion in request.suggestions:
        if suggestion.connector_id != request.connector_id:
            rejected.append(
                {
                    "mapping_id": suggestion.mapping_id,
                    "reason": "connector_id does not match request",
                }
            )
            continue
        pair = (suggestion.source_system, suggestion.source_record_id, suggestion.external_id)
        if pair not in observed:
            rejected.append(
                {
                    "mapping_id": suggestion.mapping_id,
                    "reason": "external record was not present in observed source data",
                }
            )
            continue
        accepted.append(
            suggestion.model_copy(
                update={
                    "status": "confirmed",
                    "validation_evidence": {
                        **suggestion.validation_evidence,
                        "deterministic_match": True,
                        "source_record_observed": True,
                    },
                }
            )
        )
    return MappingSuggestionReport(accepted=accepted, rejected=rejected)


def _resolve_mapping(
    *,
    internal_entity: dict[str, Any],
    internal_entity_type: str,
    internal_entity_id: str,
    external_record_type: str,
    external_records: list[dict[str, Any]],
    project_id: str | None,
    existing_mappings: list[EntityMapping],
    created_by: str | None,
    audit_sink: MappingAuditSink | None,
) -> EntityMapping:
    user_confirmed_id = internal_entity.get("user_confirmed_external_record_id")
    scored = [
        score
        for score in (
            _score_candidate(internal_entity, record, user_confirmed_id=user_confirmed_id)
            for record in external_records
        )
        if score is not None
    ]
    if not scored:
        return _pending_mapping(
            internal_entity_type=internal_entity_type,
            internal_entity_id=internal_entity_id,
            project_id=project_id,
            external_ref=_placeholder_ref(
                external_system_id=_external_system_id(external_records),
                record_type=external_record_type,
                record_id=f"unmapped-{internal_entity_id}",
            ),
            conflict_type="no_match",
            created_by=created_by,
            audit_sink=audit_sink,
        )
    scored.sort(key=lambda item: item["confidence"], reverse=True)
    best = scored[0]
    same_best = [item for item in scored if item["confidence"] == best["confidence"]]
    conflict = _detect_conflict(
        internal_entity=internal_entity,
        best=best,
        same_best=same_best,
        existing_mappings=existing_mappings,
        internal_entity_id=internal_entity_id,
    )
    external_ref = _external_ref(best["record"], external_record_type)
    if conflict in {"identifier_mismatch", "multiple_internal_map_to_one_external"}:
        return _rejected_mapping(
            internal_entity_type=internal_entity_type,
            internal_entity_id=internal_entity_id,
            project_id=project_id,
            external_ref=external_ref,
            method=_schema_method(best["signal"]),
            confidence=float(best["confidence"]),
            conflict_type=conflict,
            created_by=created_by,
            audit_sink=audit_sink,
            metadata={"matched_signal": best["signal"]},
        )
    if conflict in {
        "one_internal_maps_to_multiple_external",
        "ambiguous_name_match",
        "stale_external_version",
        "schema_mismatch",
    }:
        return _review_mapping(
            internal_entity_type=internal_entity_type,
            internal_entity_id=internal_entity_id,
            project_id=project_id,
            external_ref=external_ref,
            method=_schema_method(best["signal"]),
            confidence=float(best["confidence"]),
            conflict_type=conflict,
            created_by=created_by,
            audit_sink=audit_sink,
            metadata={"matched_signal": best["signal"], "candidate_match_count": len(same_best)},
        )
    if best["signal"] in HIGH_CONFIDENCE_SIGNALS and float(best["confidence"]) >= 0.95:
        mapping = EntityMapping(
            mapping_id=f"map-{uuid4().hex[:16]}",
            project_id=project_id,
            internal_entity_type=internal_entity_type,  # type: ignore[arg-type]
            internal_entity_id=internal_entity_id,
            external_ref=external_ref,
            mapping_method=_schema_method(best["signal"]),  # type: ignore[arg-type]
            mapping_confidence=float(best["confidence"]),
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=created_by,
            metadata={
                "matched_signal": best["signal"],
                "resolution": "auto_accept_high_confidence",
            },
        )
        _audit_mapping(mapping, audit_sink, summary="Accepted deterministic mapping.")
        return mapping
    return _review_mapping(
        internal_entity_type=internal_entity_type,
        internal_entity_id=internal_entity_id,
        project_id=project_id,
        external_ref=external_ref,
        method=_schema_method(best["signal"]),
        confidence=float(best["confidence"]),
        conflict_type="low_confidence",
        created_by=created_by,
        audit_sink=audit_sink,
        metadata={"matched_signal": best["signal"]},
    )


def _observed_identity_pairs(
    observed_records: list[ExternalRecordEnvelope],
) -> set[tuple[str, str, str]]:
    pairs: set[tuple[str, str, str]] = set()
    for record in observed_records:
        external_ids = {
            str(record.payload.get("external_id") or ""),
            str(record.payload.get("compound_registry_id") or ""),
            str(record.payload.get("registry_id") or ""),
            record.provenance.source_record_id,
        }
        for external_id in external_ids:
            if external_id:
                pairs.add(
                    (
                        record.provenance.source_system,
                        record.provenance.source_record_id,
                        external_id,
                    )
                )
    return pairs


def _score_candidate(
    internal_entity: dict[str, Any],
    external_record: dict[str, Any],
    *,
    user_confirmed_id: Any,
) -> dict[str, Any] | None:
    if user_confirmed_id and str(user_confirmed_id) == str(_external_id(external_record)):
        return {"record": external_record, "signal": "user_confirmed", "confidence": 1.0}
    signals = [
        (
            "exact_id",
            ["external_id", "external_registry_id"],
            ["external_id", "registry_id", "id"],
            1.0,
        ),
        (
            "inchi_key",
            ["inchi_key", "inchikey", "InChIKey"],
            ["inchi_key", "inchikey", "InChIKey"],
            0.99,
        ),
        (
            "canonical_smiles",
            ["canonical_smiles", "canonicalSmiles"],
            ["canonical_smiles", "canonicalSmiles"],
            0.97,
        ),
        ("chembl_id", ["chembl_id", "chembl"], ["chembl_id", "chembl"], 0.96),
        ("pubchem_cid", ["pubchem_cid", "pubchem"], ["pubchem_cid", "pubchem"], 0.96),
        ("generated_id", ["generated_id"], ["generated_id"], 0.95),
        ("registry_id", ["registry_id"], ["registry_id", "external_id"], 0.95),
        (
            "name_exact",
            ["name", "candidate_name", "molecule_name"],
            ["name", "compound_name"],
            0.75,
        ),
    ]
    for signal, internal_paths, external_paths, confidence in signals:
        internal_value = _first_value(internal_entity, internal_paths)
        external_value = _first_value(external_record, external_paths)
        if _same_identifier(internal_value, external_value):
            return {"record": external_record, "signal": signal, "confidence": confidence}
    return None


def _detect_conflict(
    *,
    internal_entity: dict[str, Any],
    best: dict[str, Any],
    same_best: list[dict[str, Any]],
    existing_mappings: list[EntityMapping],
    internal_entity_id: str,
) -> str | None:
    if len({_external_id(item["record"]) for item in same_best}) > 1:
        if best["signal"] in NAME_SIGNALS:
            return "ambiguous_name_match"
        return "one_internal_maps_to_multiple_external"
    for mapping in existing_mappings:
        if (
            mapping.external_ref.external_record_id == str(_external_id(best["record"]))
            and mapping.internal_entity_id != internal_entity_id
            and mapping.status == "active"
        ):
            return "multiple_internal_map_to_one_external"
    if _identifier_mismatch(internal_entity, best["record"]):
        return "identifier_mismatch"
    if _stale_version(internal_entity, best["record"]):
        return "stale_external_version"
    if _schema_mismatch(best["record"]):
        return "schema_mismatch"
    return None


def _identifier_mismatch(internal_entity: dict[str, Any], external_record: dict[str, Any]) -> bool:
    for internal_key, external_key in [
        ("inchi_key", "inchi_key"),
        ("canonical_smiles", "canonical_smiles"),
        ("chembl_id", "chembl_id"),
        ("pubchem_cid", "pubchem_cid"),
    ]:
        internal_value = _first_value(
            internal_entity,
            [internal_key, internal_key.replace("_", "")],
        )
        external_value = _first_value(
            external_record,
            [external_key, external_key.replace("_", "")],
        )
        if (
            internal_value
            and external_value
            and not _same_identifier(internal_value, external_value)
        ):
            return True
    return False


def _stale_version(internal_entity: dict[str, Any], external_record: dict[str, Any]) -> bool:
    expected = internal_entity.get("external_version")
    observed = external_record.get("external_version") or external_record.get("version")
    return bool(expected and observed and str(expected) != str(observed))


def _schema_mismatch(external_record: dict[str, Any]) -> bool:
    return bool(external_record.get("schema_mismatch"))


def _schema_method(signal: str) -> str:
    return {
        "chembl_id": "registry_id",
        "pubchem_cid": "registry_id",
        "generated_id": "exact_id",
    }.get(signal, signal)


def _review_mapping(
    *,
    internal_entity_type: str,
    internal_entity_id: str,
    project_id: str | None,
    external_ref: ExternalRecordRef,
    method: str,
    confidence: float,
    conflict_type: str,
    created_by: str | None,
    audit_sink: MappingAuditSink | None,
    metadata: dict[str, Any] | None = None,
) -> EntityMapping:
    mapping = _mapping(
        internal_entity_type=internal_entity_type,
        internal_entity_id=internal_entity_id,
        project_id=project_id,
        external_ref=external_ref,
        method=method,
        confidence=confidence,
        status="pending_review",
        created_by=created_by,
        metadata={"conflict_type": conflict_type, **(metadata or {})},
    )
    _audit_mapping(mapping, audit_sink, summary=f"Mapping requires review: {conflict_type}.")
    return mapping


def _pending_mapping(
    *,
    internal_entity_type: str,
    internal_entity_id: str,
    project_id: str | None,
    external_ref: ExternalRecordRef,
    conflict_type: str,
    created_by: str | None,
    audit_sink: MappingAuditSink | None,
) -> EntityMapping:
    return _review_mapping(
        internal_entity_type=internal_entity_type,
        internal_entity_id=internal_entity_id,
        project_id=project_id,
        external_ref=external_ref,
        method="manual",
        confidence=0.0,
        conflict_type=conflict_type,
        created_by=created_by,
        audit_sink=audit_sink,
    )


def _rejected_mapping(
    *,
    internal_entity_type: str,
    internal_entity_id: str,
    project_id: str | None,
    external_ref: ExternalRecordRef,
    method: str,
    confidence: float,
    conflict_type: str,
    created_by: str | None,
    audit_sink: MappingAuditSink | None,
    metadata: dict[str, Any] | None = None,
) -> EntityMapping:
    mapping = _mapping(
        internal_entity_type=internal_entity_type,
        internal_entity_id=internal_entity_id,
        project_id=project_id,
        external_ref=external_ref,
        method=method,
        confidence=confidence,
        status="rejected",
        created_by=created_by,
        metadata={"conflict_type": conflict_type, **(metadata or {})},
    )
    _audit_mapping(mapping, audit_sink, summary=f"Rejected conflicting mapping: {conflict_type}.")
    return mapping


def _mapping(
    *,
    internal_entity_type: str,
    internal_entity_id: str,
    project_id: str | None,
    external_ref: ExternalRecordRef,
    method: str,
    confidence: float,
    status: str,
    created_by: str | None,
    metadata: dict[str, Any],
) -> EntityMapping:
    return EntityMapping(
        mapping_id=f"map-{uuid4().hex[:16]}",
        project_id=project_id,
        internal_entity_type=internal_entity_type,  # type: ignore[arg-type]
        internal_entity_id=internal_entity_id,
        external_ref=external_ref,
        mapping_method=method,  # type: ignore[arg-type]
        mapping_confidence=max(0.0, min(confidence, 1.0)),
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by=created_by,
        metadata=metadata,
    )


def _audit_mapping(
    mapping: EntityMapping,
    audit_sink: MappingAuditSink | None,
    *,
    summary: str,
) -> None:
    if audit_sink is None:
        return
    audit_sink.write_audit(
        IntegrationAuditEvent(
            event_id=f"int-audit-{uuid4().hex[:16]}",
            external_system_id=mapping.external_ref.external_system_id,
            sync_job_id=None,
            actor_user_id=mapping.created_by,
            event_type="entity_mapping.resolved",
            timestamp=datetime.now(UTC),
            object_type="entity_mapping",
            object_id=mapping.mapping_id,
            summary=summary,
            metadata={
                "internal_entity_type": mapping.internal_entity_type,
                "internal_entity_id": mapping.internal_entity_id,
                "external_record_id": mapping.external_ref.external_record_id,
                "status": mapping.status,
                "mapping_method": mapping.mapping_method,
                **mapping.metadata,
            },
        )
    )


def _external_ref(record: dict[str, Any], default_type: str) -> ExternalRecordRef:
    raw_ref = record.get("external_ref")
    if isinstance(raw_ref, ExternalRecordRef):
        return raw_ref
    if isinstance(raw_ref, dict):
        return ExternalRecordRef.model_validate(raw_ref)
    return ExternalRecordRef(
        external_system_id=str(
            record.get("external_system_id") or record.get("source_system") or "external"
        ),
        external_record_type=str(
            record.get("external_record_type") or record.get("record_type") or default_type
        ),
        external_record_id=str(_external_id(record)),
        external_url=record.get("external_url") or record.get("url"),
        external_version=record.get("external_version") or record.get("version"),
        retrieved_at=datetime.now(UTC),
        metadata={},
    )


def _placeholder_ref(
    *,
    external_system_id: str,
    record_type: str,
    record_id: str,
) -> ExternalRecordRef:
    return ExternalRecordRef(
        external_system_id=external_system_id,
        external_record_type=record_type,
        external_record_id=record_id,
        retrieved_at=datetime.now(UTC),
        metadata={},
    )


def _external_id(record: dict[str, Any]) -> str:
    raw_ref = record.get("external_ref")
    if isinstance(raw_ref, ExternalRecordRef):
        return raw_ref.external_record_id
    if isinstance(raw_ref, dict) and raw_ref.get("external_record_id"):
        return str(raw_ref["external_record_id"])
    return str(
        record.get("external_record_id")
        or record.get("external_id")
        or record.get("registry_id")
        or record.get("id")
        or ""
    )


def _external_system_id(records: list[dict[str, Any]]) -> str:
    if not records:
        return "external"
    return _external_ref(records[0], "record").external_system_id


def _first_value(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = _nested(record, key)
        if value not in (None, "", []):
            return value
        identifiers = record.get("identifiers")
        if isinstance(identifiers, dict):
            value = identifiers.get(key)
            if value not in (None, "", []):
                return value
    return None


def _nested(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _same_identifier(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return False
    return str(left).strip().lower() == str(right).strip().lower()


def _required_internal_id(entity: dict[str, Any], key: str) -> str:
    value = entity.get(key) or entity.get("id")
    if not value:
        raise ValueError(f"{key} is required for mapping")
    return str(value)
