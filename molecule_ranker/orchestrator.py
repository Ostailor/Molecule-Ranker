from __future__ import annotations

from molecule_ranker.agents import (
    DiseaseResolverAgent,
    EvidenceScoringAgent,
    MoleculeRetrievalAgent,
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
from molecule_ranker.schemas import RankingRun


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
        self.evidence_scoring = EvidenceScoringAgent()
        self.report_writer = ReportWriterAgent()
        self.agents: list[BaseAgent] = [
            self.disease_resolver,
            self.target_discovery,
            self.molecule_retrieval,
            self.evidence_scoring,
            self.report_writer,
        ]

    def rank(self, disease_name: str, *, top: int | None = None) -> RankingRun:
        limit = top or self.config.default_top
        context = PipelineContext(
            disease_input=disease_name,
            config={
                "top": limit,
                "results_dir": str(self.config.results_dir),
            },
        )

        for agent in self.agents:
            context = agent.run(context)

        if context.disease is None:
            raise NoCandidatesFoundError("Disease resolution failed; no candidates can be ranked.")
        if not context.candidates:
            raise NoCandidatesFoundError("No molecule candidates were found for ranking.")
        result = RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            traces=context.traces,
            limitations=list(context.config.get("limitations", DEFAULT_LIMITATIONS)),
        )
        return result
