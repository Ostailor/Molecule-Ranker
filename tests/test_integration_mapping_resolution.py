from __future__ import annotations

from molecule_ranker.integrations.mapping import (
    codex_suggest_mapping,
    confirm_codex_mapping,
    map_candidate_to_registry_entry,
)
from molecule_ranker.integrations.schemas import ExternalRecordRef, IntegrationAuditEvent


def test_exact_inchikey_mapping_active() -> None:
    audit = FakeAuditSink()

    mapping = map_candidate_to_registry_entry(
        {
            "candidate_id": "cand-1",
            "inchi_key": "AAAA-BBBB",
            "name": "Candidate",
        },
        [
            {
                "external_system_id": "registry",
                "external_record_id": "REG-1",
                "inchi_key": "AAAA-BBBB",
                "name": "Candidate",
            }
        ],
        audit_sink=audit,
    )

    assert mapping.status == "active"
    assert mapping.mapping_method == "inchi_key"
    assert mapping.mapping_confidence == 0.99
    assert mapping.external_ref.external_record_id == "REG-1"
    assert audit.audit_events[-1].metadata["status"] == "active"


def test_name_only_ambiguous_mapping_pending_review() -> None:
    mapping = map_candidate_to_registry_entry(
        {"candidate_id": "cand-1", "name": "Same Name"},
        [
            {
                "external_system_id": "registry",
                "external_record_id": "REG-1",
                "name": "Same Name",
            },
            {
                "external_system_id": "registry",
                "external_record_id": "REG-2",
                "name": "Same Name",
            },
        ],
    )

    assert mapping.status == "pending_review"
    assert mapping.mapping_method == "name_exact"
    assert mapping.metadata["conflict_type"] == "ambiguous_name_match"


def test_conflicting_identifiers_rejected() -> None:
    mapping = map_candidate_to_registry_entry(
        {
            "candidate_id": "cand-1",
            "name": "Candidate",
            "inchi_key": "INTERNAL",
            "chembl_id": "CHEMBL1",
        },
        [
            {
                "external_system_id": "registry",
                "external_record_id": "REG-1",
                "name": "Candidate",
                "inchi_key": "EXTERNAL",
                "chembl_id": "CHEMBL1",
            }
        ],
    )

    assert mapping.status == "rejected"
    assert mapping.metadata["conflict_type"] == "identifier_mismatch"


def test_codex_suggestion_not_auto_active() -> None:
    suggestion = codex_suggest_mapping(
        internal_entity_type="candidate",
        internal_entity_id="cand-1",
        external_ref=ExternalRecordRef(
            external_system_id="registry",
            external_record_type="registry_entry",
            external_record_id="REG-1",
        ),
        confidence=0.8,
    )
    unconfirmed = confirm_codex_mapping(suggestion, deterministic_validation=False)

    assert suggestion.status == "pending_review"
    assert suggestion.mapping_method == "codex_suggested_pending_validation"
    assert unconfirmed.status == "pending_review"
    assert unconfirmed.metadata["confirmation_blocked"] is True


class FakeAuditSink:
    def __init__(self) -> None:
        self.audit_events: list[IntegrationAuditEvent] = []

    def write_audit(self, event: IntegrationAuditEvent) -> None:
        self.audit_events.append(event)
