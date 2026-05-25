from __future__ import annotations

from typing import Any

import requests
from pydantic import BaseModel, Field

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError, MoleculeRetrievalError
from molecule_ranker.schemas import Target
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class ChEMBLTargetMapping(BaseModel):
    """Resolved ChEMBL target mapping for an input disease-associated target."""

    input_target_symbol: str
    input_identifiers: dict[str, str] = Field(default_factory=dict)
    chembl_target_id: str
    target_type: str | None = None
    organism: str | None = None
    pref_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    mapping_method: str
    source_record_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChEMBLTargetMapper:
    """Map local targets to ChEMBL target IDs using stable identifiers first."""

    source_name = "ChEMBL"

    def __init__(
        self,
        *,
        base_url: str = "https://www.ebi.ac.uk/chembl/api/data",
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
        self.warnings: list[str] = []
        self.last_retry_metadata = RetryMetadata()

    def map_target(self, target: Target) -> ChEMBLTargetMapping | None:
        methods = self._mapping_methods(target)
        for method, params, confidence in methods:
            try:
                payload = self._get("target.json", {**params, "limit": 5})
            except ExternalDataUnavailableError as exc:
                self.warnings.append(
                    f"ChEMBL target mapping lookup failed for {target.symbol} "
                    f"using {method}: {exc}"
                )
                continue
            candidates = self._human_targets(payload.get("targets", []))
            mapping = self._select_mapping(
                target=target,
                candidates=candidates,
                method=method,
                confidence=confidence,
            )
            if mapping is not None:
                return mapping
            if len(candidates) > 1:
                self.warnings.append(
                    f"Ambiguous ChEMBL target mapping for {target.symbol} using {method}."
                )
                return None
        self.warnings.append(f"No ChEMBL target mapping for {target.symbol}.")
        return None

    def map_targets_or_raise(self, targets: list[Target]) -> list[ChEMBLTargetMapping]:
        mappings = [
            mapping
            for target in targets
            if (mapping := self.map_target(target)) is not None
        ]
        if not mappings:
            raise MoleculeRetrievalError(
                f"No ChEMBL target mappings found for {len(targets)} target(s)."
            )
        return mappings

    def _mapping_methods(
        self, target: Target
    ) -> list[tuple[str, dict[str, Any], float]]:
        methods: list[tuple[str, dict[str, Any], float]] = []
        uniprot = target.identifiers.get("uniprot")
        if uniprot:
            methods.append(("uniprot_accession", {"target_components__accession": uniprot}, 0.95))
        ensembl = target.identifiers.get("ensembl") or target.identifiers.get("open_targets")
        if ensembl:
            methods.append(
                (
                    "ensembl_xref",
                    {"target_components__target_component_xrefs__xref_id": ensembl},
                    0.9,
                )
            )
        if target.symbol:
            methods.append(
                (
                    "approved_symbol_exact_synonym",
                    {
                        "target_components__target_component_synonyms__component_synonym__iexact": (
                            target.symbol
                        )
                    },
                    0.8,
                )
            )
        if target.name:
            methods.append(("pref_name_contains", {"pref_name__icontains": target.name}, 0.55))
        return methods

    def _select_mapping(
        self,
        *,
        target: Target,
        candidates: list[dict[str, Any]],
        method: str,
        confidence: float,
    ) -> ChEMBLTargetMapping | None:
        if not candidates:
            return None
        if len(candidates) > 1:
            clear = self._clear_best_candidate(target, candidates)
            if clear is None:
                return None
            candidates = [clear]
        candidate = candidates[0]
        chembl_id = str(candidate.get("target_chembl_id") or "")
        if not chembl_id:
            return None
        return ChEMBLTargetMapping(
            input_target_symbol=target.symbol,
            input_identifiers=dict(target.identifiers),
            chembl_target_id=chembl_id,
            target_type=candidate.get("target_type"),
            organism=candidate.get("organism"),
            pref_name=candidate.get("pref_name"),
            confidence=confidence,
            mapping_method=method,
            source_record_id=chembl_id,
            metadata={"raw_target": candidate},
        )

    def _clear_best_candidate(
        self, target: Target, candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        symbol = target.symbol.lower()
        pref_name_matches = [
            candidate
            for candidate in candidates
            if str(candidate.get("pref_name") or "").lower() == symbol
        ]
        if len(pref_name_matches) == 1:
            return pref_name_matches[0]
        return None

    def _human_targets(self, targets: list[Any]) -> list[dict[str, Any]]:
        return [
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("target_chembl_id")
            and target.get("organism") == "Homo sapiens"
        ]

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.get(url, params=params, timeout=self.timeout_seconds),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            self.last_retry_metadata = retry_metadata
            payload = response.json()
        except requests.RequestException as exc:
            raise ExternalDataUnavailableError(f"ChEMBL target mapping failed: {exc}") from exc
        except ValueError as exc:
            raise MoleculeRetrievalError("ChEMBL target mapping returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise MoleculeRetrievalError("ChEMBL target mapping returned an unexpected payload.")
        return payload
