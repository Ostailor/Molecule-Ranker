from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from molecule_ranker.schemas import Target
from molecule_ranker.structure.schemas import StructureRecord
from molecule_ranker.structure.sources import (
    StructureSourceHealthStatus,
    timed_health_check,
    write_raw_metadata_artifact,
)
from molecule_ranker.utils.cache import JsonCache


class AlphaFoldStructureAdapter:
    """Retrieve predicted AlphaFold DB structure metadata by UniProt accession."""

    source_name = "AlphaFold_DB"
    default_base_url = "https://alphafold.ebi.ac.uk/api"
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "molecule-ranker/1.4",
    }

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        session: Any | None = None,
        cache_dir: Path | None = None,
        raw_artifact_dir: Path | None = None,
        ttl_seconds: int | None = 604800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.cache = JsonCache(cache_dir) if cache_dir is not None else None
        self.raw_artifact_dir = raw_artifact_dir
        self.ttl_seconds = ttl_seconds
        self.warnings: list[str] = []

    def retrieve(self, target: Target, *, limit: int = 10) -> list[StructureRecord]:
        self.warnings = []
        uniprots = _target_uniprot_accessions(target)
        if not uniprots:
            self.warnings.append(
                f"AlphaFold lookup skipped for {target.symbol}: missing UniProt accession."
            )
            return []
        records: list[StructureRecord] = []
        for uniprot in uniprots:
            try:
                payload = self._get_json(f"{self.base_url}/prediction/{uniprot}")
            except Exception as exc:
                self.warnings.append(f"AlphaFold lookup failed for {target.symbol}: {exc}")
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, dict):
                    records.append(self._record_from_prediction(target, uniprot, item))
                if len(records) >= limit:
                    return records
        return records

    def health_check(self) -> StructureSourceHealthStatus:
        return timed_health_check(
            source=self.source_name,
            check=lambda: {"base_url": self.base_url, "mode": "metadata_endpoint_configured"},
        )

    def _record_from_prediction(
        self,
        target: Target,
        uniprot: str,
        payload: dict[str, Any],
    ) -> StructureRecord:
        external_id = str(
            payload.get("entryId")
            or payload.get("alphafoldId")
            or payload.get("id")
            or f"AF-{uniprot}"
        )
        confidence_metadata = _confidence_metadata(payload)
        raw_artifact = write_raw_metadata_artifact(
            raw_artifact_dir=self.raw_artifact_dir,
            source=self.source_name,
            external_id=external_id,
            payload=payload,
        )
        metadata = {
            "confidence_cap": 0.55,
            "not_equivalent_to_experimental_co_crystal": True,
            "predicted_structure_lower_confidence": True,
            "cif_url": payload.get("cifUrl"),
            "pdb_url": payload.get("pdbUrl"),
            "pae_doc_url": payload.get("paeDocUrl"),
            "model_created_date": payload.get("modelCreatedDate"),
            "sequence_version": payload.get("sequenceVersion"),
        }
        if raw_artifact:
            metadata["raw_metadata_artifact"] = raw_artifact
        return StructureRecord(
            structure_id=f"{self.source_name}:{external_id}",
            source=self.source_name,
            external_id=external_id,
            target_symbol=target.symbol,
            target_identifiers={**target.identifiers, "uniprot": uniprot},
            structure_type="predicted",
            experimental_method="computed model",
            resolution_angstrom=None,
            coverage={"uniprot_accession": payload.get("uniprotAccession") or uniprot},
            chains=[],
            ligands=[],
            mutations=[],
            organism=payload.get("organismScientificName"),
            release_date=str(payload.get("modelCreatedDate"))[:10]
            if payload.get("modelCreatedDate")
            else None,
            quality_metrics={
                "confidence_metadata": confidence_metadata,
                "relative_confidence": "lower_than_suitable_experimental",
                "normalized_global_confidence": _normalize_plddt(
                    _first_float(*confidence_metadata.values())
                ),
            },
            url=payload.get("entryUrl") or f"https://alphafold.ebi.ac.uk/entry/{uniprot}",
            retrieved_at=datetime.now(UTC),
            metadata=metadata,
        )

    def _get_json(self, url: str) -> Any:
        cached = self._cache_get(url)
        if cached is not None:
            return cached
        response = self.session.get(
            url,
            headers=self.request_headers,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        self._cache_set(url, payload)
        return payload

    def _cache_get(self, url: str) -> Any | None:
        if self.cache is None:
            return None
        request = {"method": "GET", "url": url}
        key = self.cache.make_key(self.source_name, request)
        cached = self.cache.get(key, ttl_seconds=self.ttl_seconds)
        if isinstance(cached, dict) and "response" in cached:
            return cached["response"]
        return None

    def _cache_set(self, url: str, response: Any) -> None:
        if self.cache is None:
            return
        request = {"method": "GET", "url": url}
        key = self.cache.make_key(self.source_name, request)
        self.cache.set(
            key,
            {"response": response},
            source=self.source_name,
            endpoint=url,
            request=request,
            ttl_seconds=self.ttl_seconds,
        )


def _target_uniprot_accessions(target: Target) -> list[str]:
    values = []
    for key in ("uniprot", "uniprot_accession", "uniprotkb", "protein_accession"):
        value = target.identifiers.get(key)
        if value:
            values.append(str(value))
    for protein_id in target.metadata.get("protein_ids") or []:
        if not isinstance(protein_id, dict):
            continue
        source = str(protein_id.get("source") or "").lower()
        value = protein_id.get("id") or protein_id.get("accession")
        if "uniprot" in source and value:
            values.append(str(value))
    return list(dict.fromkeys(values))


def _confidence_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "global_metric_value": payload.get("globalMetricValue"),
        "average_plddt": payload.get("averagePlddt") or payload.get("avgPlddt"),
        "confidence_score": payload.get("confidenceScore"),
        "model_confidence": payload.get("modelConfidence"),
    }


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_plddt(value: float | None) -> float:
    if value is None:
        return 0.5
    if value > 1.0:
        return max(0.0, min(1.0, value / 100.0))
    return max(0.0, min(1.0, value))


__all__ = ["AlphaFoldStructureAdapter"]
