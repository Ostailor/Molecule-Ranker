from __future__ import annotations

from pathlib import Path
from typing import Any

from molecule_ranker.agents import (
    DiseaseResolverAgent,
    EvidenceScoringAgent,
    MoleculeRetrievalAgent,
    NovelMoleculeAgent,
    ReportWriterAgent,
    TargetDiscoveryAgent,
)
from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.agents.report_writer import DEFAULT_LIMITATIONS
from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources import ChEMBLAdapter, OpenTargetsAdapter
from molecule_ranker.data_sources.base import (
    DiseaseResolverDataSource,
    MoleculeAnnotationDataSource,
    MoleculeRetrievalDataSource,
    TargetDiscoveryDataSource,
)
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import MoleculeCandidate, RankingRun


class MoleculeRankerOrchestrator:
    def __init__(
        self,
        *,
        config: RankerConfig | None = None,
        disease_source: DiseaseResolverDataSource | None = None,
        target_source: TargetDiscoveryDataSource | None = None,
        molecule_source: MoleculeRetrievalDataSource | None = None,
        molecule_annotation_source: MoleculeAnnotationDataSource | None = None,
    ) -> None:
        self.config = config or RankerConfig()
        open_targets = OpenTargetsAdapter()
        self.disease_resolver = DiseaseResolverAgent(disease_source or open_targets)
        self.target_discovery = TargetDiscoveryAgent(target_source or open_targets)
        self.molecule_retrieval = MoleculeRetrievalAgent(
            molecule_source or ChEMBLAdapter(),
            molecule_annotation_source,
        )
        self.novel_molecule = NovelMoleculeAgent()
        self.evidence_scoring = EvidenceScoringAgent()
        self.report_writer = ReportWriterAgent()
        self.agents: list[BaseAgent] = [
            self.disease_resolver,
            self.target_discovery,
            self.molecule_retrieval,
            self.novel_molecule,
            self.evidence_scoring,
            self.report_writer,
        ]

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
        runtime_config: dict[str, Any] = {
            "top": limit,
            "results_dir": str(results_dir),
        }
        if config:
            runtime_config.update(config)
            runtime_config["top"] = limit
            runtime_config["results_dir"] = str(results_dir)

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
            if not self._has_real_retrieved_evidence(candidate)
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
            traces=context.traces,
            limitations=list(context.config.get("limitations", DEFAULT_LIMITATIONS)),
        )
        return result

    def _has_real_retrieved_evidence(self, candidate: MoleculeCandidate) -> bool:
        return any(item.source and item.source_record_id for item in candidate.evidence)
