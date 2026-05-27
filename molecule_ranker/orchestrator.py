from __future__ import annotations

from pathlib import Path
from typing import Any

from molecule_ranker.agents import (
    CodexBackboneAgent,
    DevelopabilityAssessmentAgent,
    DiseaseResolverAgent,
    EvidenceScoringAgent,
    ExperimentalEvidenceAgent,
    LiteratureEvidenceAgent,
    MoleculeRetrievalAgent,
    NovelMoleculeAgent,
    ReportWriterAgent,
    ReviewWorkspaceAgent,
    TargetDiscoveryAgent,
)
from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.agents.report_writer import DEFAULT_LIMITATIONS
from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources import (
    ChEMBLAdapter,
    OpenTargetsAdapter,
    PubChemAdapter,
)
from molecule_ranker.data_sources.base import (
    DiseaseResolverDataSource,
    MoleculeAnnotationDataSource,
    MoleculeRetrievalDataSource,
    TargetDiscoveryDataSource,
)
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.literature.adapters.openalex_adapter import (
    OpenAlexAdapter as LiteratureOpenAlexAdapter,
)
from molecule_ranker.literature.adapters.pubmed_adapter import (
    PubMedAdapter as LiteraturePubMedAdapter,
)
from molecule_ranker.schemas import MoleculeCandidate, RankingRun
from molecule_ranker.utils.http_cache import HttpResponseCache


class MoleculeRankerOrchestrator:
    def __init__(
        self,
        *,
        config: RankerConfig | None = None,
        disease_source: DiseaseResolverDataSource | None = None,
        target_source: TargetDiscoveryDataSource | None = None,
        molecule_source: MoleculeRetrievalDataSource | None = None,
        molecule_annotation_source: MoleculeAnnotationDataSource | None = None,
        literature_source: Any | None = None,
        literature_metadata_source: Any | None = None,
    ) -> None:
        self.config = config or RankerConfig()
        cache = HttpResponseCache(self.config.cache_dir) if self.config.use_cache else None
        open_targets = OpenTargetsAdapter(
            timeout_seconds=self.config.request_timeout_seconds,
            max_retries=self.config.max_retries,
            retry_delay_seconds=self.config.retry_backoff_seconds,
            cache=cache,
            use_cache=self.config.allow_cached_real_data,
            cache_ttl_seconds=self.config.cache_ttl_seconds,
        )
        self.disease_resolver = DiseaseResolverAgent(disease_source or open_targets)
        self.target_discovery = TargetDiscoveryAgent(target_source or open_targets)
        self.molecule_retrieval = MoleculeRetrievalAgent(
            molecule_source
            or ChEMBLAdapter(
                timeout_seconds=self.config.request_timeout_seconds,
                max_retries=self.config.max_retries,
                retry_delay_seconds=self.config.retry_backoff_seconds,
                cache=cache,
                use_cache=self.config.allow_cached_real_data,
                cache_ttl_seconds=self.config.cache_ttl_seconds,
                max_molecules_per_target=self.config.max_molecules_per_target,
                max_activity_records_per_target=self.config.max_activity_records_per_target,
                max_indications_per_molecule=self.config.max_indications_per_molecule,
                max_warnings_per_molecule=self.config.max_warnings_per_molecule,
            ),
            molecule_annotation_source
            or PubChemAdapter(
                timeout_seconds=self.config.request_timeout_seconds,
                max_retries=self.config.max_retries,
                retry_delay_seconds=self.config.retry_backoff_seconds,
                cache=cache,
                use_cache=self.config.allow_cached_real_data,
                cache_ttl_seconds=self.config.cache_ttl_seconds,
            ),
        )
        self.novel_molecule = NovelMoleculeAgent()
        literature_cache_ttl = self.config.literature_cache_ttl_seconds
        literature_timeout = self.config.literature_request_timeout_seconds
        literature_retries = self.config.literature_max_retries
        metadata_source = literature_metadata_source
        if (
            metadata_source is None
            and self.config.enable_literature
            and self.config.enable_openalex_enrichment
        ):
            metadata_source = LiteratureOpenAlexAdapter(
                timeout_seconds=literature_timeout,
                max_retries=literature_retries,
                retry_delay_seconds=self.config.retry_backoff_seconds,
                cache=cache,
                use_cache=self.config.allow_cached_real_data,
                cache_ttl_seconds=literature_cache_ttl,
                mailto=self.config.ncbi_email,
                required=self.config.strict_literature,
            )
        self.literature_evidence = None
        literature_sources = {source.lower() for source in self.config.literature_sources}
        if self.config.enable_literature and "pubmed" in literature_sources:
            self.literature_evidence = LiteratureEvidenceAgent(
                literature_source
                or LiteraturePubMedAdapter(
                    tool=self.config.ncbi_tool,
                    email=self.config.ncbi_email,
                    api_key=self.config.ncbi_api_key,
                    timeout_seconds=literature_timeout,
                    max_retries=literature_retries,
                    retry_delay_seconds=self.config.retry_backoff_seconds,
                    cache=cache,
                    use_cache=self.config.allow_cached_real_data,
                    cache_ttl_seconds=literature_cache_ttl,
                ),
                metadata_source,
            )
        self.evidence_scoring = EvidenceScoringAgent()
        self.codex_backbone = CodexBackboneAgent()
        self.experimental_evidence = ExperimentalEvidenceAgent()
        self.developability_assessment = DevelopabilityAssessmentAgent()
        self.review_workspace = ReviewWorkspaceAgent()
        self.report_writer = ReportWriterAgent()
        self.agents: list[BaseAgent] = [
            self.disease_resolver,
            self.target_discovery,
            self.molecule_retrieval,
            self.novel_molecule,
            self.developability_assessment,
            self.experimental_evidence,
            self.evidence_scoring,
            self.codex_backbone,
            self.review_workspace,
            self.report_writer,
        ]
        if self.literature_evidence is not None:
            self.agents.insert(3, self.literature_evidence)

    def rank(
        self,
        disease_input: str,
        *,
        top_n: int | None = None,
        output_dir: Path | None = None,
        config: dict[str, Any] | None = None,
        top: int | None = None,
    ) -> RankingRun:
        limit = top_n if top_n is not None else top
        limit = limit or self.config.default_top
        if limit < 1:
            raise ValueError("top_n must be at least 1.")

        results_dir = output_dir or self.config.results_dir
        runtime_config: dict[str, Any] = self.config.runtime_agent_config(
            top=limit,
            results_dir=results_dir,
        )
        if config:
            runtime_config.update(config)
            runtime_config["top"] = limit
            runtime_config["results_dir"] = str(results_dir)
            runtime_config["ranker_config"] = {
                **self.config.trace_metadata(),
                "results_dir": str(results_dir),
                "default_top": limit,
            }

        context = PipelineContext(
            disease_input=disease_input,
            config=runtime_config,
        )

        for agent in self.agents:
            context = agent.run(context)

        if context.disease is None:
            raise NoCandidatesFoundError("Disease resolution failed; no candidates can be ranked.")
        if not context.candidates:
            raise NoCandidatesFoundError("No molecule candidates were found for ranking.")
        missing_evidence = [
            candidate.name
            for candidate in context.candidates
            if not self._has_real_retrieved_evidence(candidate) and candidate.origin != "generated"
        ]
        if missing_evidence:
            raise NoCandidatesFoundError(
                "Ranked candidates require real retrieved evidence; missing evidence for "
                f"{', '.join(missing_evidence)}."
            )
        result = RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            generated_candidates=context.generated_candidates,
            traces=context.traces,
            limitations=list(context.config.get("limitations", DEFAULT_LIMITATIONS)),
        )
        return result

    def _has_real_retrieved_evidence(self, candidate: MoleculeCandidate) -> bool:
        return any(item.source and item.source_record_id for item in candidate.evidence)
