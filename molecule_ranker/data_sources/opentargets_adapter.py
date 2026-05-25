from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import requests

from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    ExternalDataUnavailableError,
    TargetDiscoveryError,
)
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.schemas import Disease, DiseaseMatch, EvidenceItem, Target
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class OpenTargetsAdapter:
    """Open Targets Platform GraphQL adapter for disease and target evidence."""

    source_name = "Open Targets"
    default_endpoint = "https://api.platform.opentargets.org/api/v4/graphql"

    def __init__(
        self,
        *,
        endpoint: str = default_endpoint,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        session: requests.Session | None = None,
        cache: HttpResponseCache | None = None,
        use_cache: bool = False,
        cache_ttl_seconds: int = 24 * 60 * 60,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self._last_response_provenance: dict[str, Any] = {"mode": "live"}
        self.last_trace_metadata: dict[str, Any] = self._empty_trace_metadata()
        self.last_disease_matches: list[DiseaseMatch] = []
        self.last_resolution_metadata: dict[str, Any] = {}

    def resolve_disease(self, disease_name: str) -> Disease:
        query = """
        query SearchDisease($queryString: String!, $size: Int!) {
          search(
            queryString: $queryString
            entityNames: ["disease"]
            page: {index: 0, size: $size}
          ) {
            hits {
              id
              name
              entity
              category
              score
            }
          }
        }
        """
        payload = self._graphql(query, {"queryString": disease_name, "size": 10})
        hits = payload.get("data", {}).get("search", {}).get("hits", [])
        if not hits:
            self._record_resolution_metadata(
                search_hit_count=0,
                selected=None,
                match_reason="no_matches",
                ambiguity=False,
                matches=[],
            )
            raise DiseaseResolutionError(f"Open Targets found no disease for {disease_name!r}.")
        self._raise_if_raw_exact_hits_are_ambiguous(disease_name, hits)
        matches = self._build_disease_matches(hits)
        selected = self._select_disease_match(disease_name, matches)
        return Disease(
            input_name=disease_name,
            canonical_name=selected.name,
            synonyms=selected.synonyms,
            identifiers=selected.identifiers,
            description=selected.description,
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
                  biotype
                  proteinIds {
                    id
                    source
                  }
                  tractability {
                    label
                    modality
                    value
                  }
                  safetyLiabilities {
                    event
                    effects {
                      direction
                      dosing
                    }
                  }
                }
              }
            }
          }
        }
        """
        payload = self._graphql(query, {"efoId": disease_id, "size": limit})
        response_provenance = dict(self._last_response_provenance)
        disease_payload = payload.get("data", {}).get("disease")
        rows = (
            disease_payload.get("associatedTargets", {}).get("rows", [])
            if isinstance(disease_payload, dict)
            else []
        )
        self._record_page_metadata(
            pages_fetched=1,
            records_fetched=len(rows) if isinstance(rows, list) else 0,
            records_retained=0,
            truncated=False,
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
            target_metadata = self._target_metadata(target_payload)
            target_metadata["association_score"] = score
            targets.append(
                Target(
                    symbol=symbol,
                    name=target_payload.get("approvedName"),
                    identifiers=self._target_identifiers(target_payload),
                    target_class=target_payload.get("biotype"),
                    tractability=target_metadata["tractability"],
                    safety=target_metadata["safety_liabilities"],
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
                                "association_score": score,
                                "target_metadata": target_metadata,
                                "response_provenance": response_provenance,
                            },
                        )
                    ],
                    mechanism=None,
                    metadata=target_metadata,
                )
            )
        targets.sort(key=lambda target: target.disease_relevance_score, reverse=True)
        self.last_trace_metadata["records_retained"] += len(targets)
        if not targets:
            raise TargetDiscoveryError(
                f"Open Targets returned no associated targets for {disease.canonical_name!r}."
            )
        return targets

    def health_check(self, *, timeout_seconds: float = 5.0) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        query = "query HealthCheck { __typename }"
        started = perf_counter()
        try:
            response = self.session.post(
                self.endpoint,
                json={"query": query, "variables": {}},
                timeout=min(timeout_seconds, self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise ExternalDataUnavailableError(
                    f"Open Targets GraphQL error: {payload['errors']}"
                )
        except Exception as exc:  # pragma: no cover - exact failures are source-dependent
            return AdapterHealthStatus(
                source_name=self.source_name,
                ok=False,
                endpoint=self.endpoint,
                checked_at=checked_at,
                latency_ms=self._elapsed_ms(started),
                error=str(exc),
                metadata={"probe": "graphql_typename"},
            )
        return AdapterHealthStatus(
            source_name=self.source_name,
            ok=True,
            endpoint=self.endpoint,
            checked_at=checked_at,
            latency_ms=self._elapsed_ms(started),
            error=None,
            metadata={"probe": "graphql_typename"},
        )

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
        request_payload = {"query": query, "variables": variables}
        cache_key = self._cache_key(request_payload)
        retry_metadata = RetryMetadata()
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.post(
                    self.endpoint,
                    json=request_payload,
                    timeout=self.timeout_seconds,
                ),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            payload = response.json()
        except requests.RequestException as exc:
            retry_metadata = getattr(exc, "retry_metadata", retry_metadata)
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError(f"Open Targets request failed: {exc}") from exc
        except ValueError as exc:
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError("Open Targets returned invalid JSON.") from exc
        if payload.get("errors"):
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError(f"Open Targets GraphQL error: {payload['errors']}")
        response_metadata = self._response_metadata(request_payload, response, retry_metadata)
        self._record_retry_metadata(retry_metadata)
        self._write_cache(cache_key, request_payload, payload, response_metadata)
        self._last_response_provenance = response_metadata
        return payload

    def _cache_key(self, request_payload: dict[str, Any]) -> str | None:
        if self.cache is None:
            return None
        return self.cache.build_key(
            source_name=self.source_name,
            endpoint=self.endpoint,
            method="POST",
            graphql_variables=dict(request_payload.get("variables", {})),
            request_body=request_payload,
        )

    def _cached_response(self, cache_key: str | None) -> CachedHttpResponse | None:
        if not self.use_cache or self.cache is None or cache_key is None:
            return None
        return self.cache.get(cache_key, ttl_seconds=self.cache_ttl_seconds)

    def _write_cache(
        self,
        cache_key: str | None,
        request_payload: dict[str, Any],
        payload: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.cache is None or cache_key is None:
            return
        self.cache.write_success(
            cache_key=cache_key,
            response_json=payload,
            source=self.source_name,
            endpoint=self.endpoint,
            method="POST",
            request_metadata={
                "graphql_variables": dict(request_payload.get("variables", {})),
                "body_hash_basis": "query_and_variables",
            },
            ttl_seconds=self.cache_ttl_seconds,
            response_metadata=response_metadata or {},
        )

    def _response_metadata(
        self,
        request_payload: dict[str, Any],
        response: requests.Response,
        retry_metadata: RetryMetadata,
    ) -> dict[str, Any]:
        return {
            "mode": "live",
            "source": self.source_name,
            "endpoint": self.endpoint,
            "graphql_variables": dict(request_payload.get("variables", {})),
            "status_code": int(getattr(response, "status_code", 200)),
            "retry_count": retry_metadata.retry_count,
            "rate_limit_retry_count": retry_metadata.rate_limit_retry_count,
            "attempts": retry_metadata.attempts,
            "status_codes": list(retry_metadata.status_codes),
        }

    def _empty_trace_metadata(self) -> dict[str, Any]:
        return {
            "pages_fetched": 0,
            "records_fetched": 0,
            "records_retained": 0,
            "truncated": False,
            "retry_count": 0,
            "rate_limit_retry_count": 0,
        }

    def _record_retry_metadata(self, metadata: RetryMetadata) -> None:
        self.last_trace_metadata["retry_count"] += metadata.retry_count
        self.last_trace_metadata["rate_limit_retry_count"] += metadata.rate_limit_retry_count

    def _record_page_metadata(
        self,
        *,
        pages_fetched: int,
        records_fetched: int,
        records_retained: int,
        truncated: bool,
    ) -> None:
        self.last_trace_metadata["pages_fetched"] += pages_fetched
        self.last_trace_metadata["records_fetched"] += records_fetched
        self.last_trace_metadata["records_retained"] += records_retained
        self.last_trace_metadata["truncated"] = bool(
            self.last_trace_metadata["truncated"] or truncated
        )

    def _raise_if_raw_exact_hits_are_ambiguous(
        self, disease_name: str, hits: list[dict[str, Any]]
    ) -> None:
        disease_hits = [hit for hit in hits if str(hit.get("entity") or "").lower() == "disease"]
        if not disease_hits:
            disease_hits = hits
        normalized_query = self._normalize_name(disease_name)
        raw_exact_hits = [
            hit
            for hit in disease_hits
            if self._normalize_name(str(hit.get("name") or "")) == normalized_query
        ]
        distinct_exact_ids = {str(hit.get("id")) for hit in raw_exact_hits if hit.get("id")}
        if len(distinct_exact_ids) > 1:
            matches = [
                self._match_from_hit(hit, "ambiguous_exact_canonical_match")
                for hit in raw_exact_hits
            ]
            self.last_disease_matches = matches
            self._record_resolution_metadata(
                search_hit_count=len(hits),
                selected=None,
                match_reason="ambiguous_exact_canonical_match",
                ambiguity=True,
                matches=matches,
            )
            raise self._ambiguous_error(matches)

    def _build_disease_matches(self, hits: list[dict[str, Any]]) -> list[DiseaseMatch]:
        matches: list[DiseaseMatch] = []
        for hit in hits:
            if str(hit.get("entity") or "").lower() != "disease":
                continue
            disease_id = str(hit.get("id") or "")
            if not disease_id:
                continue
            details = self._disease_details(disease_id)
            name = str(details.get("name") or hit.get("name") or disease_id)
            matches.append(
                DiseaseMatch(
                    id=disease_id,
                    name=name,
                    entity=str(hit.get("entity") or "disease"),
                    score=self._optional_float(hit.get("score")),
                    synonyms=self._extract_synonyms(details),
                    description=details.get("description"),
                    identifiers=self._extract_identifiers(disease_id, details),
                    match_reason="candidate",
                )
            )
        if not matches:
            self._record_resolution_metadata(
                search_hit_count=len(hits),
                selected=None,
                match_reason="no_disease_matches",
                ambiguity=False,
                matches=[],
            )
            raise DiseaseResolutionError("Open Targets returned no disease search hits.")
        self.last_disease_matches = matches
        return matches

    def _select_disease_match(
        self, disease_name: str, matches: list[DiseaseMatch]
    ) -> DiseaseMatch:
        normalized_query = self._normalize_name(disease_name)

        exact_canonical = [
            match for match in matches if self._normalize_name(match.name) == normalized_query
        ]
        if len(exact_canonical) == 1:
            return self._accept_match(exact_canonical[0], "exact_canonical_match", matches)
        if len(exact_canonical) > 1:
            self._record_resolution_metadata(
                search_hit_count=len(matches),
                selected=None,
                match_reason="ambiguous_exact_canonical_match",
                ambiguity=True,
                matches=matches,
            )
            raise self._ambiguous_error(exact_canonical)

        exact_synonym = [
            match
            for match in matches
            if any(self._normalize_name(synonym) == normalized_query for synonym in match.synonyms)
        ]
        if len(exact_synonym) == 1:
            return self._accept_match(exact_synonym[0], "exact_synonym_match", matches)
        if len(exact_synonym) > 1:
            self._record_resolution_metadata(
                search_hit_count=len(matches),
                selected=None,
                match_reason="ambiguous_exact_synonym_match",
                ambiguity=True,
                matches=matches,
            )
            raise self._ambiguous_error(exact_synonym)

        scored_hits = sorted(
            matches,
            key=lambda match: match.score or 0.0,
            reverse=True,
        )
        top = scored_hits[0]
        second_score = scored_hits[1].score if len(scored_hits) > 1 else 0.0
        top_score = top.score or 0.0
        margin = top_score - (second_score or 0.0)
        if top_score >= 0.75 and margin >= 0.15:
            return self._accept_match(top, "high_confidence_margin", matches)

        self._record_resolution_metadata(
            search_hit_count=len(matches),
            selected=None,
            match_reason="ambiguous_low_margin",
            ambiguity=True,
            matches=matches,
        )
        raise self._ambiguous_error(scored_hits)

    def _accept_match(
        self, match: DiseaseMatch, match_reason: str, matches: list[DiseaseMatch]
    ) -> DiseaseMatch:
        selected = match.model_copy(update={"match_reason": match_reason})
        self.last_disease_matches = [
            item.model_copy(
                update={"match_reason": match_reason if item.id == selected.id else "candidate"}
            )
            for item in matches
        ]
        self._record_resolution_metadata(
            search_hit_count=len(matches),
            selected=selected,
            match_reason=match_reason,
            ambiguity=False,
            matches=matches,
        )
        return selected

    def _record_resolution_metadata(
        self,
        *,
        search_hit_count: int,
        selected: DiseaseMatch | None,
        match_reason: str,
        ambiguity: bool,
        matches: list[DiseaseMatch],
    ) -> None:
        self.last_resolution_metadata = {
            "search_hit_count": search_hit_count,
            "selected_disease_id": selected.id if selected else None,
            "selected_disease_name": selected.name if selected else None,
            "match_reason": match_reason,
            "ambiguity": ambiguity,
            "top_matches": [
                {"id": match.id, "name": match.name, "score": match.score}
                for match in matches[:10]
            ],
        }

    def _ambiguous_error(self, matches: list[DiseaseMatch]) -> DiseaseResolutionError:
        top_matches = ", ".join(
            f"{match.name} ({match.id})" for match in matches[:5]
        )
        return DiseaseResolutionError(f"Disease input was ambiguous. Top matches: {top_matches}")

    def _match_from_hit(self, hit: dict[str, Any], match_reason: str) -> DiseaseMatch:
        disease_id = str(hit.get("id") or "")
        return DiseaseMatch(
            id=disease_id,
            name=str(hit.get("name") or disease_id),
            entity=str(hit.get("entity") or "disease"),
            score=self._optional_float(hit.get("score")),
            synonyms=[],
            description=None,
            identifiers={"open_targets": disease_id} if disease_id else {},
            match_reason=match_reason,
        )

    def _optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_name(self, value: str) -> str:
        return " ".join(value.casefold().replace("-", " ").split())

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)

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

    def _target_identifiers(self, target_payload: dict[str, Any]) -> dict[str, str]:
        identifiers: dict[str, str] = {}
        target_id = target_payload.get("id")
        if target_id:
            identifiers["ensembl"] = str(target_id)
            identifiers["open_targets"] = str(target_id)
        for protein_id in target_payload.get("proteinIds", []) or []:
            if not isinstance(protein_id, dict):
                continue
            protein_value = protein_id.get("id")
            if not protein_value:
                continue
            source = str(protein_id.get("source") or "").lower()
            if "uniprot" in source:
                identifiers.setdefault("uniprot", str(protein_value))
            identifiers.setdefault(source or "protein_id", str(protein_value))
        return identifiers

    def _target_metadata(self, target_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "association_score": None,
            "approved_symbol": target_payload.get("approvedSymbol"),
            "approved_name": target_payload.get("approvedName"),
            "biotype": target_payload.get("biotype"),
            "tractability": target_payload.get("tractability") or [],
            "safety_liabilities": target_payload.get("safetyLiabilities") or [],
            "protein_ids": target_payload.get("proteinIds") or [],
        }
