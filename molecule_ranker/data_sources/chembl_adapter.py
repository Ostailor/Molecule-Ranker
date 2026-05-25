from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import requests

from molecule_ranker.data_sources.errors import (
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
)
from molecule_ranker.schemas import Disease, Target


class ChEMBLAdapter:
    """ChEMBL REST adapter for target-linked molecule and mechanism retrieval.

    Target lookup and mechanism records are required evidence sources. Molecule
    detail lookup is optional enrichment: when a mechanism record already
    identifies an existing ChEMBL molecule, a detail lookup failure preserves the
    evidence-backed record with a warning instead of fabricating replacement
    metadata.
    """

    source_name = "ChEMBL"
    default_base_url = "https://www.ebi.ac.uk/chembl/api/data"

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        records_by_id: dict[str, dict[str, Any]] = {}
        for target in targets:
            target_ids = self._target_chembl_ids(target.symbol)
            for target_id in target_ids:
                mechanisms = self._get(
                    "mechanism.json",
                    {"target_chembl_id": target_id, "limit": limit_per_target},
                ).get("mechanisms", [])
                for mechanism in mechanisms:
                    molecule_id = mechanism.get("molecule_chembl_id")
                    if not molecule_id:
                        continue
                    warnings: list[str] = []
                    try:
                        molecule = self._molecule_details(str(molecule_id))
                    except ExternalDataUnavailableError as exc:
                        molecule = {}
                        warnings.append(
                            "Optional ChEMBL molecule-detail enrichment unavailable "
                            f"for {molecule_id}: {exc}"
                        )
                    record = self._record_from_mechanism(
                        disease=disease,
                        target=target,
                        target_chembl_id=target_id,
                        mechanism=mechanism,
                        molecule=molecule,
                    )
                    record["warnings"].extend(warnings)
                    existing = records_by_id.get(str(molecule_id))
                    if existing is None:
                        records_by_id[str(molecule_id)] = record
                    else:
                        existing["known_targets"] = sorted(
                            set(existing["known_targets"]) | set(record["known_targets"])
                        )
                        existing["evidence"].extend(record["evidence"])
                        existing["warnings"] = sorted(
                            set(existing.get("warnings", [])) | set(record["warnings"])
                        )
        records = list(records_by_id.values())
        if not records:
            raise NoCandidatesFoundError(
                f"ChEMBL found no molecule mechanisms for {len(targets)} target(s)."
            )
        return records

    def _target_chembl_ids(self, symbol: str) -> list[str]:
        payload = self._get(
            "target.json",
            {
                "target_components__target_component_synonyms__component_synonym__iexact": symbol,
                "limit": 5,
            },
        )
        ids = [
            str(target["target_chembl_id"])
            for target in payload.get("targets", [])
            if target.get("target_chembl_id") and target.get("organism") == "Homo sapiens"
        ]
        return ids

    def _molecule_details(self, molecule_chembl_id: str) -> dict[str, Any]:
        payload = self._get(
            "molecule.json",
            {"molecule_chembl_id": molecule_chembl_id, "limit": 1},
        )
        molecules = payload.get("molecules", [])
        return molecules[0] if molecules else {}

    def _record_from_mechanism(
        self,
        *,
        disease: Disease,
        target: Target,
        target_chembl_id: str,
        mechanism: dict[str, Any],
        molecule: dict[str, Any],
    ) -> dict[str, Any]:
        molecule_id = str(mechanism["molecule_chembl_id"])
        max_phase = mechanism.get("max_phase")
        clinical_precedence = (
            max(0.0, min(float(max_phase) / 4.0, 1.0)) if max_phase is not None else 0.0
        )
        direct_interaction = bool(mechanism.get("direct_interaction"))
        target_fit = 0.8 if direct_interaction else 0.5
        retrieved_at = datetime.now(UTC).isoformat()
        pref_name = molecule.get("pref_name") or molecule_id
        return {
            "name": str(pref_name),
            "molecule_type": molecule.get("molecule_type") or "unknown",
            "identifiers": {"chembl": molecule_id},
            "known_targets": [target.symbol],
            "development_status": (
                f"max_phase_{max_phase}" if max_phase is not None else None
            ),
            "mechanism_of_action": mechanism.get("mechanism_of_action")
            or mechanism.get("mechanism_comment"),
            "target_fit": target_fit,
            "clinical_precedence": clinical_precedence,
            "safety_prior": 0.5,
            "repurposing_value": 0.5,
            "warnings": [],
            "evidence": [
                {
                    "source": self.source_name,
                    "source_record_id": str(mechanism.get("mec_id") or mechanism.get("record_id")),
                    "title": f"ChEMBL mechanism for {target.symbol}",
                    "url": f"https://www.ebi.ac.uk/chembl/mechanism_report_card/{molecule_id}/",
                    "evidence_type": "mechanism",
                    "summary": (
                        f"ChEMBL reports {mechanism.get('action_type') or 'an action'} "
                        f"for {molecule_id} on {target.symbol} in the context of "
                        f"{disease.canonical_name} target retrieval."
                    ),
                    "confidence": target_fit,
                    "retrieval_timestamp": retrieved_at,
                    "metadata": {
                        "target_chembl_id": target_chembl_id,
                        "molecule_chembl_id": molecule_id,
                        "action_type": mechanism.get("action_type"),
                        "max_phase": max_phase,
                    },
                }
            ],
        }

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: requests.RequestException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                status_code = getattr(response, "status_code", 200)
                if status_code >= 500 and attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise MoleculeRetrievalError("ChEMBL returned an unexpected payload.")
                return payload
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise ExternalDataUnavailableError(f"ChEMBL request failed: {exc}") from exc
            except ValueError as exc:
                raise MoleculeRetrievalError("ChEMBL returned invalid JSON.") from exc
        else:  # pragma: no cover - loop always returns or raises
            raise ExternalDataUnavailableError(f"ChEMBL request failed: {last_error}")
