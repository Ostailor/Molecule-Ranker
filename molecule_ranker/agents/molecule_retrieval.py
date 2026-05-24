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

        limit_per_target = int(context.config.get("limit_per_target", 10))
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
        context.config[f"{self.name}.summary"] = (
            f"Retrieved {len(records)} raw molecule records and retained {len(candidates)} "
            "deduplicated evidence-backed candidates."
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return str(context.config.get(f"{self.name}.summary", "Retrieved molecules."))

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {
            "targets_queried": len(context.targets),
            "sources_used": self._sources_used(),
            "raw_molecule_records": context.config.get(f"{self.name}.raw_count", 0),
            "deduplicated_molecules": context.config.get(
                f"{self.name}.deduplicated_count", len(context.candidates)
            ),
            "deduplication_identifiers": context.config.get(
                f"{self.name}.deduplication_identifiers", []
            ),
        }

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
        for record in records:
            if not self._has_real_evidence(record):
                continue
            key = self._dedup_key(record)
            existing = deduplicated.get(key)
            if existing is None:
                deduplicated[key] = dict(record)
                deduplicated[key]["known_targets"] = sorted(set(record.get("known_targets", [])))
                deduplicated[key]["evidence"] = list(record.get("evidence", []))
                continue
            existing["known_targets"] = sorted(
                set(existing.get("known_targets", [])) | set(record.get("known_targets", []))
            )
            existing["evidence"] = self._merge_evidence(
                list(existing.get("evidence", [])),
                list(record.get("evidence", [])),
            )
            existing["identifiers"] = {
                **dict(record.get("identifiers", {})),
                **dict(existing.get("identifiers", {})),
            }
        return deduplicated

    def _dedup_key(self, record: dict[str, Any]) -> str:
        identifiers = {
            str(k).lower(): str(v)
            for k, v in dict(record.get("identifiers", {})).items()
        }
        for key in ("chembl", "chembl_id", "pubchem_cid", "cid", "inchikey", "inchi_key"):
            value = identifiers.get(key)
            if value:
                normalized_key = "chembl" if key in {"chembl", "chembl_id"} else key
                return f"{normalized_key}:{value}"
        name = str(record.get("name") or "").strip().lower()
        normalized_name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
        if normalized_name:
            return f"name:{normalized_name}"
        raise MoleculeRetrievalError("Molecule record lacks stable identifiers and name.")

    def _merge_evidence(
        self, existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in [*existing, *incoming]:
            key = str(item.get("source_record_id") or item.get("title") or item)
            merged.setdefault(key, item)
        return list(merged.values())

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
            evidence=[EvidenceItem(**item) for item in record.get("evidence", [])],
            score=None,
            score_breakdown=None,
            warnings=[],
        )
