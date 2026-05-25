from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    ExternalDataUnavailableError,
    TargetDiscoveryError,
)
from molecule_ranker.schemas import Disease, EvidenceItem, Target


class OpenTargetsAdapter:
    """Open Targets Platform GraphQL adapter for disease and target evidence."""

    source_name = "Open Targets"
    default_endpoint = "https://api.platform.opentargets.org/api/v4/graphql"

    def __init__(
        self,
        *,
        endpoint: str = default_endpoint,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def resolve_disease(self, disease_name: str) -> Disease:
        # V0.0 uses the top Open Targets search hit. V0.1 should request a
        # wider result page and add ambiguity handling plus confidence thresholds
        # before accepting a canonical disease entity.
        query = """
        query SearchDisease($queryString: String!) {
          search(queryString: $queryString, entityNames: ["disease"], page: {index: 0, size: 1}) {
            hits {
              id
              name
              entity
            }
          }
        }
        """
        payload = self._graphql(query, {"queryString": disease_name})
        hits = payload.get("data", {}).get("search", {}).get("hits", [])
        if not hits:
            raise DiseaseResolutionError(f"Open Targets found no disease for {disease_name!r}.")
        hit = hits[0]
        disease_id = str(hit["id"])
        details = self._disease_details(disease_id)
        return Disease(
            input_name=disease_name,
            canonical_name=str(details.get("name") or hit["name"]),
            synonyms=self._extract_synonyms(details),
            identifiers=self._extract_identifiers(disease_id, details),
            description=details.get("description"),
        )

    def discover_targets(self, disease: Disease, *, limit: int = 100) -> list[Target]:
        disease_id = disease.identifiers.get("open_targets")
        if not disease_id:
            raise TargetDiscoveryError("Disease is missing an Open Targets identifier.")
        query = """
        query DiseaseTargets($efoId: String!, $size: Int!) {
          disease(efoId: $efoId) {
            id
            name
            associatedTargets(page: {index: 0, size: $size}) {
              rows {
                score
                target {
                  id
                  approvedSymbol
                  approvedName
                }
              }
            }
          }
        }
        """
        payload = self._graphql(query, {"efoId": disease_id, "size": limit})
        disease_payload = payload.get("data", {}).get("disease")
        rows = (
            disease_payload.get("associatedTargets", {}).get("rows", [])
            if isinstance(disease_payload, dict)
            else []
        )
        targets: list[Target] = []
        retrieved_at = datetime.now(UTC)
        for row in rows:
            target_payload = row.get("target") or {}
            target_id = str(target_payload.get("id") or "")
            symbol = str(target_payload.get("approvedSymbol") or "")
            score = float(row.get("score") or 0.0)
            if not target_id or not symbol:
                continue
            targets.append(
                Target(
                    symbol=symbol,
                    name=target_payload.get("approvedName"),
                    disease_relevance_score=max(0.0, min(score, 1.0)),
                    evidence=[
                        EvidenceItem(
                            source=self.source_name,
                            source_record_id=f"{disease_id}:{target_id}",
                            title=f"{symbol} association with {disease.canonical_name}",
                            url=(
                                "https://platform.opentargets.org/evidence/"
                                f"{target_id}/{disease_id}"
                            ),
                            evidence_type="target_disease_association",
                            summary=(
                                f"Open Targets reports a target-disease association score "
                                f"of {score:.3f} for {symbol} and {disease.canonical_name}."
                            ),
                            confidence=max(0.0, min(score, 1.0)),
                            retrieval_timestamp=retrieved_at,
                            metadata={
                                "query": "disease.associatedTargets",
                                "disease_id": disease_id,
                                "target_id": target_id,
                            },
                        )
                    ],
                    mechanism=None,
                )
            )
        targets.sort(key=lambda target: target.disease_relevance_score, reverse=True)
        if not targets:
            raise TargetDiscoveryError(
                f"Open Targets returned no associated targets for {disease.canonical_name!r}."
            )
        return targets

    def _disease_details(self, disease_id: str) -> dict[str, Any]:
        query = """
        query DiseaseDetails($efoId: String!) {
          disease(efoId: $efoId) {
            id
            name
            description
            dbXRefs
            synonyms {
              terms
              relation
            }
          }
        }
        """
        payload = self._graphql(query, {"efoId": disease_id})
        disease = payload.get("data", {}).get("disease")
        return disease if isinstance(disease, dict) else {}

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.endpoint,
                json={"query": query, "variables": variables},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ExternalDataUnavailableError(f"Open Targets request failed: {exc}") from exc
        except ValueError as exc:
            raise ExternalDataUnavailableError("Open Targets returned invalid JSON.") from exc
        if payload.get("errors"):
            raise ExternalDataUnavailableError(f"Open Targets GraphQL error: {payload['errors']}")
        return payload

    def _extract_synonyms(self, details: dict[str, Any]) -> list[str]:
        synonyms: list[str] = []
        for group in details.get("synonyms", []) or []:
            for term in group.get("terms", []) or []:
                if isinstance(term, str) and term not in synonyms:
                    synonyms.append(term)
        return synonyms

    def _extract_identifiers(self, disease_id: str, details: dict[str, Any]) -> dict[str, str]:
        identifiers = {"open_targets": disease_id}
        normalized_primary = disease_id.replace("_", ":")
        if ":" in normalized_primary:
            prefix, _ = normalized_primary.split(":", 1)
            identifiers[prefix.lower()] = normalized_primary
        for xref in details.get("dbXRefs", []) or []:
            if not isinstance(xref, str) or ":" not in xref:
                continue
            prefix, _ = xref.split(":", 1)
            key = prefix.lower().replace(".", "_")
            identifiers.setdefault(key, xref)
        return identifiers
