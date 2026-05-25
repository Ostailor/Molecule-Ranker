from __future__ import annotations

import re
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.data_sources.base import (
    MoleculeAnnotationDataSource,
    MoleculeRetrievalDataSource,
)
from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.errors import MoleculeRetrievalError, NoCandidatesFoundError
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate


class MoleculeRetrievalAgent(BaseAgent):
    name = "MoleculeRetrievalAgent"

    def __init__(
        self,
        data_source: MoleculeRetrievalDataSource | None = None,
        annotation_source: MoleculeAnnotationDataSource | None = None,
    ) -> None:
        super().__init__()
        self._data_source = data_source or ChEMBLAdapter()
        self._annotation_source = annotation_source or PubChemAdapter()

    def process(self, context: PipelineContext) -> PipelineContext:
        if not context.targets:
            raise MoleculeRetrievalError("Molecule retrieval requires discovered targets.")
        if context.disease is None:
            raise MoleculeRetrievalError("Molecule retrieval requires a resolved disease.")

        limit_per_target = int(
            context.config.get(
                "max_molecules_per_target",
                context.config.get("limit_per_target", 10),
            )
        )
        records = self._data_source.retrieve_molecules(
            context.disease,
            context.targets,
            limit_per_target=limit_per_target,
        )
        context.config[f"{self.name}.raw_count"] = len(records)
        if not records:
            raise NoCandidatesFoundError("No molecule records returned by molecule data source.")

        annotated_records = self._annotation_source.annotate_molecules(records)
        deduplicated = self._deduplicate_records(annotated_records)
        dedup_audit = getattr(self, "_last_dedup_audit", {})
        candidates = [
            self._candidate_from_record(record)
            for record in deduplicated.values()
            if self._has_real_evidence(record)
        ]
        if not candidates:
            raise NoCandidatesFoundError("No evidence-backed molecule candidates were found.")

        context.candidates = candidates
        context.config["molecule_records"] = annotated_records
        context.config[f"{self.name}.deduplicated_count"] = len(candidates)
        context.config[f"{self.name}.deduplication_identifiers"] = list(deduplicated.keys())
        context.config[f"{self.name}.duplicate_keys_seen"] = dedup_audit.get(
            "duplicate_keys_seen", []
        )
        context.config[f"{self.name}.merge_count"] = dedup_audit.get("merge_count", 0)
        context.config[f"{self.name}.merge_sources"] = dedup_audit.get("merge_sources", [])
        context.config[f"{self.name}.deduplication_method"] = dedup_audit.get(
            "deduplication_method", {}
        )
        context.config[f"{self.name}.summary"] = (
            f"Retrieved {len(records)} raw molecule records and retained {len(candidates)} "
            "deduplicated evidence-backed candidates."
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return str(context.config.get(f"{self.name}.summary", "Retrieved molecules."))

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        metadata = {
            "targets_queried": len(context.targets),
            "sources_used": self._sources_used(),
            "raw_molecule_records": context.config.get(f"{self.name}.raw_count", 0),
            "deduplicated_molecules": context.config.get(
                f"{self.name}.deduplicated_count", len(context.candidates)
            ),
            "deduplication_identifiers": context.config.get(
                f"{self.name}.deduplication_identifiers", []
            ),
            "duplicate_keys_seen": context.config.get(
                f"{self.name}.duplicate_keys_seen", []
            ),
            "merge_count": context.config.get(f"{self.name}.merge_count", 0),
            "merge_sources": context.config.get(f"{self.name}.merge_sources", []),
            "deduplication_method": context.config.get(
                f"{self.name}.deduplication_method", {}
            ),
        }
        metadata.update(self._adapter_trace_metadata())
        return metadata

    def _adapter_trace_metadata(self) -> dict[str, object]:
        data_trace = getattr(self._data_source, "last_trace_metadata", {})
        annotation_trace = getattr(self._annotation_source, "last_trace_metadata", {})
        combined: dict[str, object] = {}
        for prefix, trace in (
            ("retrieval", data_trace),
            ("annotation", annotation_trace),
        ):
            if isinstance(trace, dict):
                for key in (
                    "pages_fetched",
                    "records_fetched",
                    "records_retained",
                    "truncated",
                    "retry_count",
                    "rate_limit_retry_count",
                ):
                    combined[f"{prefix}_{key}"] = trace.get(key)
        return combined

    def _sources_used(self) -> list[str]:
        sources = [getattr(self._data_source, "source_name", self._data_source.__class__.__name__)]
        annotation_name = getattr(
            self._annotation_source,
            "source_name",
            self._annotation_source.__class__.__name__,
        )
        if annotation_name not in sources:
            sources.append(annotation_name)
        return sources

    def _deduplicate_records(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        deduplicated: dict[str, dict[str, Any]] = {}
        audit: dict[str, Any] = {
            "duplicate_keys_seen": [],
            "merge_count": 0,
            "merge_sources": set(),
            "deduplication_method": {},
        }
        for record in records:
            if not self._has_real_evidence(record):
                continue
            key, method = self._dedup_key(record)
            audit["duplicate_keys_seen"].append(key)
            audit["deduplication_method"][key] = method
            existing = deduplicated.get(key)
            if existing is None:
                initial = dict(record)
                initial["known_targets"] = sorted(set(record.get("known_targets", [])))
                initial["evidence"] = list(record.get("evidence", []))
                initial["warnings"] = list(record.get("warnings", []))
                if method == "name":
                    initial["warnings"].append(
                        "Low-confidence normalized-name-only deduplication used; "
                        "stable chemistry identifiers were unavailable."
                    )
                deduplicated[key] = initial
                self._add_merge_sources(audit, initial)
                continue
            audit["merge_count"] += 1
            self._merge_record(existing, record, method)
            self._add_merge_sources(audit, existing)
        audit["merge_sources"] = sorted(audit["merge_sources"])
        self._last_dedup_audit = audit
        return deduplicated

    def _dedup_key(self, record: dict[str, Any]) -> tuple[str, str]:
        identifiers = {
            str(k).lower(): str(v)
            for k, v in dict(record.get("identifiers", {})).items()
            if v not in (None, "")
        }
        chemical_metadata = {
            str(k).lower(): str(v)
            for k, v in dict(record.get("chemical_metadata", {})).items()
            if v not in (None, "")
        }
        priority = [
            (
                "chembl_parent",
                self._first_value(
                    identifiers,
                    chemical_metadata,
                    keys=(
                        "chembl_parent",
                        "chembl_parent_id",
                        "parent_chembl_id",
                        "parent_molecule_chembl_id",
                        "molecule_parent_chembl_id",
                    ),
                ),
            ),
            (
                "inchikey",
                self._first_value(
                    identifiers,
                    chemical_metadata,
                    keys=("inchikey", "inchi_key"),
                ),
            ),
            (
                "pubchem_cid",
                self._first_value(identifiers, chemical_metadata, keys=("pubchem_cid", "cid")),
            ),
            (
                "chembl",
                self._first_value(
                    identifiers,
                    chemical_metadata,
                    keys=("chembl", "chembl_id", "molecule_chembl_id"),
                ),
            ),
        ]
        for method, value in priority:
            if value:
                return f"{method}:{value}", method
        name = str(record.get("name") or "").strip().lower()
        normalized_name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
        if normalized_name:
            return f"name:{normalized_name}", "name"
        raise MoleculeRetrievalError("Molecule record lacks stable identifiers and name.")

    def _merge_record(
        self,
        existing: dict[str, Any],
        record: dict[str, Any],
        method: str,
    ) -> None:
        existing["known_targets"] = sorted(
            set(existing.get("known_targets", [])) | set(record.get("known_targets", []))
        )
        existing["evidence"] = self._merge_evidence(
            list(existing.get("evidence", [])),
            list(record.get("evidence", [])),
        )
        existing["warnings"] = sorted(
            set(existing.get("warnings", []))
            | set(record.get("warnings", []))
            | set(self._identifier_conflict_warnings(existing, record))
            | set(self._chemical_metadata_conflict_warnings(existing, record))
        )
        if method == "name":
            existing["warnings"] = sorted(
                set(existing["warnings"])
                | {
                    "Low-confidence normalized-name-only deduplication used; "
                    "stable chemistry identifiers were unavailable."
                }
            )
        existing["identifiers"] = self._merge_mapping_values(
            dict(existing.get("identifiers", {})),
            dict(record.get("identifiers", {})),
        )
        existing["chemical_metadata"] = self._merge_mapping_values(
            dict(existing.get("chemical_metadata", {})),
            dict(record.get("chemical_metadata", {})),
        )
        existing["development_status"] = self._higher_confidence_development_status(
            existing.get("development_status"),
            record.get("development_status"),
        )

    def _merge_evidence(
        self, existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in [*existing, *incoming]:
            key = self._evidence_key(item)
            merged.setdefault(key, item)
        return list(merged.values())

    def _evidence_key(self, item: dict[str, Any]) -> str:
        source = str(item.get("source") or "unknown")
        record_id = item.get("source_record_id")
        if record_id not in (None, ""):
            return f"{source}:{record_id}"
        return f"{source}:{item.get('title') or item}"

    def _merge_mapping_values(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            if value in (None, ""):
                continue
            merged.setdefault(str(key), value)
        return merged

    def _identifier_conflict_warnings(
        self, existing: dict[str, Any], record: dict[str, Any]
    ) -> list[str]:
        return self._conflict_warnings(
            dict(existing.get("identifiers", {})),
            dict(record.get("identifiers", {})),
            label="identifier",
        )

    def _chemical_metadata_conflict_warnings(
        self, existing: dict[str, Any], record: dict[str, Any]
    ) -> list[str]:
        return self._conflict_warnings(
            dict(existing.get("chemical_metadata", {})),
            dict(record.get("chemical_metadata", {})),
            label="chemical metadata",
        )

    def _conflict_warnings(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
        *,
        label: str,
    ) -> list[str]:
        warnings: list[str] = []
        for key, incoming_value in incoming.items():
            existing_value = existing.get(key)
            if (
                existing_value not in (None, "")
                and incoming_value not in (None, "")
                and str(existing_value) != str(incoming_value)
            ):
                warnings.append(
                    f"Conflicting {label} for {key}: retained {existing_value}; "
                    f"also observed {incoming_value}."
                )
        return warnings

    def _higher_confidence_development_status(
        self, existing: Any, incoming: Any
    ) -> str | None:
        if existing in (None, ""):
            return str(incoming) if incoming not in (None, "") else None
        if incoming in (None, ""):
            return str(existing)
        existing_status = str(existing)
        incoming_status = str(incoming)
        return (
            incoming_status
            if self._development_status_rank(incoming_status)
            > self._development_status_rank(existing_status)
            else existing_status
        )

    def _development_status_rank(self, status: str) -> float:
        normalized = status.lower().replace("-", "_")
        if "approved" in normalized or "max_phase_4" in normalized or "phase_4" in normalized:
            return 4.0
        match = re.search(r"(?:max_phase_|phase_?)([0-4])", normalized)
        if match:
            return float(match.group(1))
        if "clinical" in normalized:
            return 1.0
        if "preclinical" in normalized:
            return 0.5
        return 0.0

    def _first_value(
        self,
        identifiers: dict[str, str],
        chemical_metadata: dict[str, str],
        *,
        keys: tuple[str, ...],
    ) -> str | None:
        for key in keys:
            value = identifiers.get(key) or chemical_metadata.get(key)
            if value:
                return value
        return None

    def _add_merge_sources(self, audit: dict[str, Any], record: dict[str, Any]) -> None:
        sources = audit["merge_sources"]
        for item in record.get("evidence", []):
            source = item.get("source")
            if source:
                sources.add(str(source))

    def _has_real_evidence(self, record: dict[str, Any]) -> bool:
        evidence = record.get("evidence", [])
        return any(item.get("source") and item.get("source_record_id") for item in evidence)

    def _candidate_from_record(self, record: dict[str, Any]) -> MoleculeCandidate:
        return MoleculeCandidate(
            name=str(record.get("name") or "Unnamed molecule"),
            molecule_type=str(record.get("molecule_type") or "unknown"),
            identifiers={str(k): str(v) for k, v in dict(record.get("identifiers", {})).items()},
            known_targets=[str(target) for target in record.get("known_targets", [])],
            development_status=record.get("development_status"),
            mechanism_of_action=record.get("mechanism_of_action"),
            chemical_metadata=dict(record.get("chemical_metadata", {})),
            evidence=[EvidenceItem(**item) for item in record.get("evidence", [])],
            score=None,
            score_breakdown=None,
            warnings=[str(warning) for warning in record.get("warnings", [])],
        )
