from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.data_sources.errors import EvidenceRetrievalError, ExternalDataUnavailableError
from molecule_ranker.literature.adapters.base import (
    LiteratureMetadataAdapter,
    LiteratureSearchAdapter,
)
from molecule_ranker.literature.adapters.pubmed_adapter import PubMedAdapter
from molecule_ranker.literature.errors import LiteratureParsingError, LiteratureRetrievalError
from molecule_ranker.literature.extraction.claim_extractor import extract_claims
from molecule_ranker.literature.extraction.evidence_classifier import classify_evidence
from molecule_ranker.literature.normalizer import literature_evidence_item
from molecule_ranker.literature.query_builder import build_literature_queries
from molecule_ranker.literature.schemas import (
    EvidenceClaim as ModuleEvidenceClaim,
)
from molecule_ranker.literature.schemas import (
    LiteratureEvidenceBundle as ModuleLiteratureEvidenceBundle,
)
from molecule_ranker.literature.schemas import (
    LiteraturePaper as ModuleLiteraturePaper,
)
from molecule_ranker.literature.schemas import (
    LiteratureQuery as ModuleLiteratureQuery,
)
from molecule_ranker.schemas import (
    Citation,
    EvidenceClaim,
    EvidenceItem,
    LiteratureEvidenceBundle,
    LiteratureEvidenceItem,
    LiteraturePaper,
    LiteratureQuery,
    MoleculeCandidate,
    Target,
)

ABSENT_REASON = "No literature records with conservative supported claims."


class LiteratureEvidenceAgent(BaseAgent):
    """Retrieve papers and extract conservative literature claims without fabrication."""

    name = "LiteratureEvidenceAgent"

    def __init__(
        self,
        search_adapter: LiteratureSearchAdapter | Any | None = None,
        metadata_adapter: LiteratureMetadataAdapter | Any | None = None,
    ) -> None:
        super().__init__()
        self._search_adapter = search_adapter or PubMedAdapter()
        self._metadata_adapter = metadata_adapter

    def process(self, context: PipelineContext) -> PipelineContext:
        if context.disease is None:
            raise EvidenceRetrievalError("Literature retrieval requires a resolved disease.")
        if not context.candidates:
            raise EvidenceRetrievalError("Literature retrieval requires molecule candidates.")

        strict_literature = self._strict_literature(context.config)
        queries = build_literature_queries(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            config=context.config,
        )
        papers_by_key: dict[str, ModuleLiteraturePaper] = {}
        query_papers: dict[str, list[ModuleLiteraturePaper]] = {}
        warnings: list[str] = []
        failures: list[str] = []
        queries_executed = 0
        papers_retrieved = 0

        for query in queries:
            try:
                papers = self._search(query)
                queries_executed += 1
                papers_retrieved += len(papers)
                papers = self._enrich(papers)
            except (
                ExternalDataUnavailableError,
                LiteratureRetrievalError,
                LiteratureParsingError,
            ) as exc:
                if strict_literature:
                    raise
                warning = f"Literature query {query.query_id} skipped: {exc}"
                warnings.append(warning)
                failures.append(warning)
                continue

            retained: list[ModuleLiteraturePaper] = []
            for paper in papers:
                key = self._paper_key(paper)
                papers_by_key.setdefault(key, paper)
                retained.append(papers_by_key[key])
            query_papers[query.query_id] = retained

        claims_by_query = self._extract_claims(
            queries=queries,
            query_papers=query_papers,
        )
        candidate_claims = [
            claim
            for claims in claims_by_query.values()
            for claim in claims
            if claim.candidate_name
        ]
        target_claims = [
            claim
            for claims in claims_by_query.values()
            for claim in claims
            if claim.claim_type == "disease_target_association" and claim.target_symbol
        ]

        context.candidates = [
            self._candidate_with_literature(candidate, queries, query_papers, candidate_claims)
            for candidate in context.candidates
        ]
        context.targets = [
            self._target_with_literature(target, target_claims, papers_by_key)
            for target in context.targets
        ]
        bundles = self._module_bundles(queries, query_papers, claims_by_query, warnings)
        literature_config = {
            "queries_generated": len(queries),
            "queries_executed": queries_executed,
            "papers_retrieved": papers_retrieved,
            "unique_papers_retained": len(papers_by_key),
            "claims_extracted": sum(len(claims) for claims in claims_by_query.values()),
            "sources_used": self._sources_used(papers_by_key.values()),
            "failures": failures,
            "warnings": warnings,
            "strict_literature": strict_literature,
            "bundles": [bundle.model_dump(mode="json") for bundle in bundles],
        }
        context.config["literature_evidence"] = literature_config
        context.config.setdefault("warnings", []).extend(warnings)
        context.config[f"{self.name}.queries_generated"] = len(queries)
        context.config[f"{self.name}.queries_executed"] = queries_executed
        context.config[f"{self.name}.papers_retrieved"] = papers_retrieved
        context.config[f"{self.name}.unique_papers_retained"] = len(papers_by_key)
        context.config[f"{self.name}.claims_extracted"] = literature_config["claims_extracted"]
        context.config[f"{self.name}.sources_used"] = literature_config["sources_used"]
        context.config[f"{self.name}.failures"] = failures
        context.config[f"{self.name}.warnings"] = warnings
        context.config[f"{self.name}.strict_literature"] = strict_literature
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        evidence = dict(context.config.get("literature_evidence", {}))
        return (
            f"Executed {evidence.get('queries_executed', 0)} literature queries and "
            f"extracted {evidence.get('claims_extracted', 0)} conservative claims."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {
            "queries_generated": context.config.get(f"{self.name}.queries_generated", 0),
            "queries_executed": context.config.get(f"{self.name}.queries_executed", 0),
            "papers_retrieved": context.config.get(f"{self.name}.papers_retrieved", 0),
            "unique_papers_retained": context.config.get(
                f"{self.name}.unique_papers_retained", 0
            ),
            "claims_extracted": context.config.get(f"{self.name}.claims_extracted", 0),
            "sources_used": context.config.get(f"{self.name}.sources_used", []),
            "failures": context.config.get(f"{self.name}.failures", []),
            "warnings": context.config.get(f"{self.name}.warnings", []),
            "strict_literature": context.config.get(f"{self.name}.strict_literature", False),
        }

    def _search(self, query: ModuleLiteratureQuery) -> list[ModuleLiteraturePaper]:
        search = getattr(self._search_adapter, "search", None)
        retrieve_papers = getattr(self._search_adapter, "retrieve_papers", None)
        if callable(search):
            papers: Any = search(query)
        elif callable(retrieve_papers):
            papers = retrieve_papers(self._legacy_query(query))
        else:
            raise LiteratureRetrievalError("Literature search adapter lacks search method.")
        if not isinstance(papers, list):
            raise LiteratureRetrievalError("Literature search adapter returned invalid papers.")
        return [self._normalize_paper(paper) for paper in papers]

    def _enrich(self, papers: list[ModuleLiteraturePaper]) -> list[ModuleLiteraturePaper]:
        if self._metadata_adapter is None or not papers:
            return papers
        enrich = getattr(self._metadata_adapter, "enrich", None)
        enrich_papers = getattr(self._metadata_adapter, "enrich_papers", None)
        if callable(enrich):
            enriched: Any = enrich(papers)
            if not isinstance(enriched, list):
                return papers
            return list(enriched)
        if callable(enrich_papers):
            legacy = [self._legacy_paper(paper) for paper in papers]
            enriched = enrich_papers(legacy)
            if not isinstance(enriched, list):
                return papers
            return [self._normalize_paper(paper) for paper in enriched]
        return papers

    def _extract_claims(
        self,
        *,
        queries: list[ModuleLiteratureQuery],
        query_papers: dict[str, list[ModuleLiteraturePaper]],
    ) -> dict[str, list[ModuleEvidenceClaim]]:
        claims_by_query: dict[str, list[ModuleEvidenceClaim]] = {}
        for query in queries:
            claims: list[ModuleEvidenceClaim] = []
            for paper in query_papers.get(query.query_id, []):
                claims.extend(self._claims_for_query(query, paper))
            claims_by_query[query.query_id] = claims
        return claims_by_query

    def _claims_for_query(
        self,
        query: ModuleLiteratureQuery,
        paper: ModuleLiteraturePaper,
    ) -> list[ModuleEvidenceClaim]:
        claims = extract_claims(
            paper=paper,
            query=query,
            disease_name=query.disease_name,
            target_symbol=query.target_symbol,
            target_name=query.target_name,
            molecule_name=query.molecule_name,
            molecule_synonyms=self._molecule_synonyms(query),
        )
        claims_with_query_text = [
            claim.model_copy(
                update={"metadata": {**claim.metadata, "query_text": query.query_text}}
            )
            for claim in claims
        ]
        return [classify_evidence(paper, claim) for claim in claims_with_query_text]

    def _candidate_with_literature(
        self,
        candidate: MoleculeCandidate,
        queries: list[ModuleLiteratureQuery],
        query_papers: dict[str, list[ModuleLiteraturePaper]],
        claims: list[ModuleEvidenceClaim],
    ) -> MoleculeCandidate:
        candidate_claims = [
            claim for claim in claims if claim.candidate_name == candidate.name
        ]
        evidence = list(candidate.evidence)
        for claim in candidate_claims:
            paper = self._paper_for_claim(claim, query_papers)
            if paper is not None:
                item = self._evidence_item(claim, paper)
                if item is not None:
                    evidence.append(item)
        legacy_bundle = self._legacy_candidate_bundle(
            candidate,
            queries,
            query_papers,
            candidate_claims,
        )
        warnings = list(candidate.warnings)
        if not candidate_claims:
            warnings.append("Literature evidence is absent for this candidate.")
        return candidate.model_copy(
            update={
                "evidence": evidence,
                "literature_evidence": legacy_bundle,
                "warnings": warnings,
            }
        )

    def _target_with_literature(
        self,
        target: Target,
        claims: list[ModuleEvidenceClaim],
        papers_by_key: dict[str, ModuleLiteraturePaper],
    ) -> Target:
        papers_by_id = {paper.paper_id: paper for paper in papers_by_key.values()}
        target_claims = [claim for claim in claims if claim.target_symbol == target.symbol]
        evidence = list(target.evidence)
        for claim in target_claims:
            paper = papers_by_id.get(claim.paper_id)
            if paper is not None:
                item = self._evidence_item(claim, paper)
                if item is not None:
                    evidence.append(item)
        return target.model_copy(update={"evidence": evidence})

    def _module_bundles(
        self,
        queries: list[ModuleLiteratureQuery],
        query_papers: dict[str, list[ModuleLiteraturePaper]],
        claims_by_query: dict[str, list[ModuleEvidenceClaim]],
        warnings: list[str],
    ) -> list[ModuleLiteratureEvidenceBundle]:
        return [
            ModuleLiteratureEvidenceBundle(
                query=query,
                papers=query_papers.get(query.query_id, []),
                claims=claims_by_query.get(query.query_id, []),
                warnings=warnings,
                metadata={"query_text": query.query_text, "query_type": query.query_type},
            )
            for query in queries
        ]

    def _legacy_candidate_bundle(
        self,
        candidate: MoleculeCandidate,
        queries: list[ModuleLiteratureQuery],
        query_papers: dict[str, list[ModuleLiteraturePaper]],
        claims: list[ModuleEvidenceClaim],
    ) -> LiteratureEvidenceBundle:
        items: list[LiteratureEvidenceItem] = []
        seen_papers: set[str] = set()
        for claim in claims:
            paper = self._paper_for_claim(claim, query_papers)
            query = next(
                (
                    query
                    for query in queries
                    if query.query_id == claim.metadata["query_id"]
                ),
                None,
            )
            if paper is None or query is None:
                continue
            if paper.paper_id in seen_papers:
                continue
            seen_papers.add(paper.paper_id)
            legacy_paper = self._legacy_paper(paper)
            old_claim = EvidenceClaim(
                claim_type=claim.claim_type,
                text=self._legacy_claim_text(claim, paper),
                matched_terms=[
                    term
                    for term in (claim.candidate_name, claim.target_symbol, claim.disease_name)
                    if term
                ],
                study_type=self._study_type(paper),
                support_level="mentions",
                cautions=[
                    "Rule-based extraction from title/abstract/metadata only.",
                    "This is not evidence that the molecule treats or cures the disease.",
                ],
            )
            items.append(
                LiteratureEvidenceItem(
                    query=self._legacy_query(query),
                    paper=legacy_paper,
                    citation=Citation.from_paper(legacy_paper),
                    claims=[old_claim],
                    quality_score=self._claim_confidence(paper),
                )
            )
        return LiteratureEvidenceBundle(
            candidate_name=candidate.name,
            query_count=len(queries),
            items=items,
            quality_score=self._bundle_quality(items),
            absent_reason=None if items else ABSENT_REASON,
            metadata={"claims_extracted": len(claims)},
        )

    def _evidence_item(
        self,
        claim: ModuleEvidenceClaim,
        paper: ModuleLiteraturePaper,
    ) -> EvidenceItem | None:
        return literature_evidence_item(paper, claim)

    def _normalize_paper(self, paper: Any) -> ModuleLiteraturePaper:
        if isinstance(paper, ModuleLiteraturePaper):
            return paper
        publication_date = getattr(paper, "publication_date", None)
        date_text: str | None
        year: int | None
        if isinstance(publication_date, date):
            date_text = publication_date.isoformat()
            year = publication_date.year
        elif isinstance(publication_date, str):
            date_text = publication_date
            year = self._year_from_date_text(publication_date)
        else:
            date_text = None
            year = None
        publication_types = list(getattr(paper, "publication_types", []) or [])
        retrieved_at = getattr(paper, "retrieval_timestamp", datetime.now(UTC))
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        source = str(getattr(paper, "source", "PubMed"))
        source_record_id = str(
            getattr(paper, "source_record_id", None)
            or getattr(paper, "pmid", None)
            or getattr(paper, "doi", None)
            or "unknown"
        )
        pmid = getattr(paper, "pmid", None)
        doi = getattr(paper, "doi", None)
        return ModuleLiteraturePaper(
            paper_id=f"pubmed:{pmid}" if pmid else f"{source.lower()}:{source_record_id}",
            source=source,
            title=str(getattr(paper, "title", "Untitled literature record")),
            abstract=getattr(paper, "abstract", None),
            authors=list(getattr(paper, "authors", []) or []),
            journal=getattr(paper, "journal", None),
            publication_date=date_text,
            year=year,
            doi=doi,
            pmid=pmid,
            pmcid=dict(getattr(paper, "metadata", {}) or {}).get("pmcid"),
            openalex_id=dict(getattr(paper, "metadata", {}) or {}).get("openalex_id"),
            publication_type=publication_types[0] if publication_types else None,
            is_review=any("review" in value.lower() for value in publication_types),
            is_clinical=self._text_has_clinical_terms(
                str(getattr(paper, "title", "")),
                getattr(paper, "abstract", None),
                publication_types,
            ),
            is_preclinical=self._text_has_preclinical_terms(
                str(getattr(paper, "title", "")),
                getattr(paper, "abstract", None),
                publication_types,
            ),
            is_retracted=bool(getattr(paper, "is_retracted", False)),
            cited_by_count=getattr(paper, "citation_count", None),
            url=getattr(paper, "url", None),
            retrieved_at=retrieved_at,
            metadata=dict(getattr(paper, "metadata", {}) or {}),
        )

    def _legacy_paper(self, paper: ModuleLiteraturePaper) -> LiteraturePaper:
        publication_date = self._date_from_text(paper.publication_date)
        publication_types = [paper.publication_type] if paper.publication_type else []
        return LiteraturePaper(
            source=paper.source,
            source_record_id=paper.paper_id,
            title=paper.title,
            abstract=paper.abstract,
            pmid=paper.pmid,
            doi=paper.doi,
            journal=paper.journal,
            publication_date=publication_date,
            publication_types=publication_types,
            authors=paper.authors,
            url=paper.url,
            is_open_access=paper.metadata.get("open_access", {}).get("is_oa")
            if isinstance(paper.metadata.get("open_access"), dict)
            else None,
            is_retracted=bool(paper.is_retracted),
            citation_count=paper.cited_by_count,
            retrieval_timestamp=paper.retrieved_at,
            metadata={
                **paper.metadata,
                "pmcid": paper.pmcid,
                "openalex_id": paper.openalex_id,
            },
        )

    def _legacy_query(self, query: ModuleLiteratureQuery) -> LiteratureQuery:
        return LiteratureQuery(
            disease=query.disease_name,
            molecule=query.molecule_name,
            target=query.target_symbol,
            query_text=query.query_text,
            max_results=query.max_results,
            metadata=query.metadata,
        )

    def _paper_for_claim(
        self,
        claim: ModuleEvidenceClaim,
        query_papers: dict[str, list[ModuleLiteraturePaper]],
    ) -> ModuleLiteraturePaper | None:
        query_id = str(claim.metadata.get("query_id"))
        for paper in query_papers.get(query_id, []):
            if paper.paper_id == claim.paper_id:
                return paper
        return None

    def _paper_key(self, paper: ModuleLiteraturePaper) -> str:
        if paper.pmid:
            return f"pmid:{paper.pmid}"
        if paper.doi:
            return f"doi:{paper.doi.lower()}"
        return self._paper_key_from_id(paper.paper_id)

    def _paper_key_from_id(self, paper_id: str) -> str:
        return f"id:{paper_id}"

    def _sources_used(self, papers: Iterable[ModuleLiteraturePaper]) -> list[str]:
        sources = {getattr(self._search_adapter, "source_name", "PubMed")}
        if self._metadata_adapter is not None:
            sources.add(getattr(self._metadata_adapter, "source_name", "OpenAlex"))
        sources.update(paper.source for paper in papers)
        return sorted(sources)

    def _strict_literature(self, config: dict[str, Any]) -> bool:
        if "strict_literature" in config:
            return bool(config["strict_literature"])
        return str(config.get("literature_failure_policy", "skip")).lower() == "fail"

    def _molecule_synonyms(self, query: ModuleLiteratureQuery) -> list[str]:
        source_terms = query.metadata.get("source_terms", [])
        if not isinstance(source_terms, list):
            return []
        excluded = {
            str(term).lower()
            for term in (
                query.molecule_name,
                query.target_symbol,
                query.target_name,
                query.disease_name,
            )
            if term
        }
        return [
            str(term)
            for term in source_terms
            if term and str(term).lower() not in excluded
        ]

    def _legacy_claim_text(
        self,
        claim: ModuleEvidenceClaim,
        paper: ModuleLiteraturePaper,
    ) -> str:
        matched_terms = [
            term
            for term in (claim.candidate_name, claim.target_symbol, claim.disease_name)
            if term
        ]
        if matched_terms:
            terms = ", ".join(matched_terms)
            return (
                f"{paper.source} paper {paper.paper_id} mentions {terms}; "
                "this requires validation."
            )
        return claim.claim_text

    def _claim_confidence(self, paper: ModuleLiteraturePaper) -> float:
        confidence = 0.55
        if paper.abstract:
            confidence += 0.1
        if paper.pmid or paper.doi:
            confidence += 0.1
        if paper.is_clinical:
            confidence += 0.1
        return min(confidence, 1.0)

    def _bundle_quality(self, items: list[LiteratureEvidenceItem]) -> float:
        if not items:
            return 0.0
        return min(sum(item.quality_score for item in items) / len(items), 1.0)

    def _study_type(self, paper: ModuleLiteraturePaper) -> str:
        if paper.is_clinical:
            return "clinical"
        if paper.is_preclinical:
            return "preclinical"
        if paper.is_review:
            return "review"
        return "unknown"

    def _text_has_clinical_terms(
        self,
        title: str,
        abstract: str | None,
        publication_types: list[str],
    ) -> bool:
        text = " ".join([title, abstract or "", *publication_types]).lower()
        return "clinical trial" in text or "randomized" in text or "phase " in text

    def _text_has_preclinical_terms(
        self,
        title: str,
        abstract: str | None,
        publication_types: list[str],
    ) -> bool:
        text = " ".join([title, abstract or "", *publication_types]).lower()
        return any(
            term in text
            for term in ("preclinical", "mouse", "mice", "cell", "in vitro", "model")
        )

    def _year_from_date_text(self, value: str) -> int | None:
        try:
            return int(value[:4])
        except (TypeError, ValueError):
            return None

    def _date_from_text(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
