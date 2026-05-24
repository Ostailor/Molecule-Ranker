from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.agents import (
    DiseaseResolverAgent,
    EvidenceScoringAgent,
    MoleculeRetrievalAgent,
    ReportWriterAgent,
    TargetDiscoveryAgent,
)
from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources import ChEMBLAdapter, OpenTargetsAdapter
from molecule_ranker.data_sources.base import (
    DiseaseResolverDataSource,
    MoleculeRetrievalDataSource,
    TargetDiscoveryDataSource,
)
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import RankingRun
from molecule_ranker.utils import slugify


class MoleculeRankerOrchestrator:
    def __init__(
        self,
        *,
        config: RankerConfig | None = None,
        disease_source: DiseaseResolverDataSource | None = None,
        target_source: TargetDiscoveryDataSource | None = None,
        molecule_source: MoleculeRetrievalDataSource | None = None,
    ) -> None:
        self.config = config or RankerConfig()
        open_targets = OpenTargetsAdapter()
        self.disease_resolver = DiseaseResolverAgent(disease_source or open_targets)
        self.target_discovery = TargetDiscoveryAgent(target_source or open_targets)
        self.molecule_retrieval = MoleculeRetrievalAgent(molecule_source or ChEMBLAdapter())
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
        output_dir = self.config.results_dir / slugify(disease_name)
        context = PipelineContext(
            disease_input=disease_name,
            config={
                "top": limit,
                "results_dir": str(self.config.results_dir),
            },
            output_dir=output_dir,
        )

        for agent in self.agents:
            context = agent.run(context)

        if context.disease is None:
            raise NoCandidatesFoundError("Disease resolution failed; no candidates can be ranked.")
        if not context.candidates:
            raise NoCandidatesFoundError("No molecule candidates were found for ranking.")
        output_dir = self.config.results_dir / slugify(context.disease.canonical_name)
        context.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        result = RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            traces=context.traces,
            limitations=[
                "Results depend on public external biomedical sources available at retrieval time.",
                "Rankings are research hypotheses, not medical advice.",
                "Therapeutic relevance requires experimental validation.",
            ],
        )
        self._write_outputs(result, output_dir, str(context.config.get("report_md", "")))
        return result

    def _write_outputs(self, result: RankingRun, output_dir: Path, report: str) -> None:
        (output_dir / "candidates.json").write_text(
            _json_dumps(
                {
                    "disease": result.disease,
                    "targets": result.targets,
                    "candidates": result.candidates,
                    "limitations": result.limitations,
                }
            )
        )
        (output_dir / "report.md").write_text(report)
        (output_dir / "trace.json").write_text(
            _json_dumps(
                {
                    "disease": result.disease,
                    "traces": result.traces,
                    "limitations": result.limitations,
                    "artifacts": {
                        "candidates_json": str(output_dir / "candidates.json"),
                        "report_md": str(output_dir / "report.md"),
                        "trace_json": str(output_dir / "trace.json"),
                    },
                }
            )
        )


def _json_dumps(payload: dict[str, Any]) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    return json.dumps(payload, default=default, indent=2, sort_keys=True) + "\n"
