from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from molecule_ranker.agent_graph import (
    AgentEdge,
    AgentExecutionResult,
    AgentGraphExecutor,
    AgentGraphRun,
    AgentNode,
)
from molecule_ranker.agents.experiment_readiness import (
    ExperimentReadinessAgent as RuleBasedExperimentReadinessAgent,
)
from molecule_ranker.agents.medicinal_chemistry_critic import (
    MedicinalChemistryCriticAgent as RuleBasedMedicinalChemistryCriticAgent,
)
from molecule_ranker.design.active_design import ActiveLearningDesignPlanner
from molecule_ranker.design.oracles import MultiObjectiveOracleStack, OracleStackResult
from molecule_ranker.design.uncertainty import UncertaintyEstimator

SCIENTIFIC_DESIGN_AGENT_NAMES = [
    "ScientificDesignPlannerAgent",
    "DesignObjectiveAgent",
    "SeedAndScaffoldSelectionAgent",
    "GeneratorEnsembleAgent",
    "OracleScoringAgent",
    "MedicinalChemistryCriticAgent",
    "UncertaintyAndDiversityAgent",
    "ExperimentReadinessAgent",
    "ActiveLearningDesignAgent",
]


class ScientificDesignPlannerGraphNode:
    name = "ScientificDesignPlannerAgent"

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        state["scientific_design_plan"] = {
            "version": "1.1",
            "goal": "prioritize generated hypotheses for experimental triage review",
            "claim_boundary": "planning and critique only, not biomedical truth",
        }
        return state


class DesignObjectiveAgent:
    name = "DesignObjectiveAgent"

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        objectives = state.get("objectives", [])
        state["design_objectives"] = [
            {
                "objective_id": getattr(objective, "objective_id", None),
                "target_symbol": getattr(objective, "target_symbol", None),
                "objective_type": getattr(objective, "objective_type", None),
                "seed_count": len(getattr(objective, "seed_molecule_ids", []) or []),
                "constraints": sorted((getattr(objective, "constraints", {}) or {}).keys()),
            }
            for objective in objectives
        ]
        return state


class SeedAndScaffoldSelectionAgent:
    name = "SeedAndScaffoldSelectionAgent"

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        seeds = state.get("seeds", [])
        state["seed_scaffold_selection"] = {
            "seed_count": len(seeds),
            "scaffold_policy": "evidence-backed retrieved seeds only",
            "selected_seed_ids": [self._seed_id(seed) for seed in seeds],
        }
        return state

    def _seed_id(self, seed: Any) -> str:
        identifiers = getattr(seed, "identifiers", {}) or {}
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            if identifiers.get(key):
                return str(identifiers[key])
        return str(getattr(seed, "name", "unknown-seed"))


class GeneratorEnsembleAgent:
    name = "GeneratorEnsembleAgent"

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        generated = state.get("generated", [])
        methods: dict[str, int] = {}
        for candidate in generated:
            method = str(getattr(candidate, "generation_method", "unknown"))
            methods[method] = methods.get(method, 0) + 1
        state["generator_ensemble"] = {
            "methods": methods or {"selfies_mutation": 0},
            "deterministic_validation_required": True,
        }
        return state


class OracleScoringAgent:
    name = "OracleScoringAgent"

    def __init__(self) -> None:
        self._oracle_stack = MultiObjectiveOracleStack()

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        retained = state.get("retained", [])
        objectives = state.get("objectives", [])
        seeds = state.get("seeds", [])
        config = state.get("config", {})
        oracle_results = []
        retained_so_far = []
        for candidate in retained:
            objective = next(
                (
                    item
                    for item in objectives
                    if getattr(item, "objective_id", None)
                    == getattr(candidate, "objective_id", None)
                ),
                None,
            )
            parent_seeds = [
                seed
                for seed in seeds
                if self._seed_id(seed) in set(getattr(candidate, "parent_seed_ids", []))
            ]
            result = self._oracle_stack.score(
                candidate=candidate,
                objective=objective,
                seeds=parent_seeds,
                retained_generated=retained_so_far,
                enable_docking=bool(
                    config.get("enable_docking_oracle", False)
                    if isinstance(config, dict)
                    else False
                ),
                enable_surrogate=bool(
                    config.get("enable_surrogate_activity_oracle", False)
                    if isinstance(config, dict)
                    else False
                ),
            )
            oracle_results.append(result.model_dump(mode="json"))
            retained_so_far.append(candidate)
        state["oracle_scoring"] = {
            "scored_count": len(retained),
            "score_name": "experiment_worthiness_score",
            "results": oracle_results,
            "claim_boundary": "not predicted efficacy; not predicted binding",
        }
        return state

    def _seed_id(self, seed: Any) -> str:
        identifiers = getattr(seed, "identifiers", {}) or {}
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            if identifiers.get(key):
                return str(identifiers[key])
        return str(getattr(seed, "name", "unknown-seed"))


class MedicinalChemistryCriticAgent:
    name = "MedicinalChemistryCriticAgent"

    def __init__(self) -> None:
        self._critic = RuleBasedMedicinalChemistryCriticAgent()

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        retained = state.get("retained", [])
        objectives = state.get("objectives", [])
        seeds = state.get("seeds", [])
        critiques = [
            critique.model_dump(mode="json")
            for critique in self._critic.critique_state(
                generated=retained,
                objectives=objectives,
                seeds=seeds,
            )
        ]
        state["medicinal_chemistry_critique"] = {
            "reviewed_count": len(retained),
            "scope": "non-protocol computational critique",
            "protocol_content": False,
            "critiques": critiques,
        }
        return state


class UncertaintyAndDiversityAgent:
    name = "UncertaintyAndDiversityAgent"

    def __init__(self) -> None:
        self._estimator = UncertaintyEstimator()

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        retained = state.get("retained", [])
        seeds = state.get("seeds", [])
        known_candidates = state.get("candidates", [])
        oracle_results = {
            str(item.get("generated_id")): item
            for item in (
                state.get("oracle_scoring", {}).get("results", [])
                if isinstance(state.get("oracle_scoring"), dict)
                else []
            )
            if isinstance(item, dict)
        }
        estimates = []
        for candidate in retained:
            raw_oracle_result = oracle_results.get(str(getattr(candidate, "generated_id", "")))
            oracle_result = (
                OracleStackResult.model_validate(raw_oracle_result)
                if raw_oracle_result is not None
                else None
            )
            estimates.append(
                self._estimator.estimate(
                    candidate=candidate,
                    seeds=seeds,
                    known_candidates=known_candidates,
                    oracle_result=oracle_result,
                ).model_dump(mode="json")
            )
        clusters = {
            getattr(candidate, "diversity_cluster", None)
            for candidate in retained
            if getattr(candidate, "diversity_cluster", None)
        }
        state["uncertainty_and_diversity"] = {
            "retained_count": len(retained),
            "diversity_cluster_count": len(clusters),
            "uncertainty_estimates": estimates,
            "claim_boundary": "uncertainty describes computational triage only",
        }
        return state


class ExperimentReadinessAgent:
    name = "ExperimentReadinessAgent"

    def __init__(self) -> None:
        self._readiness_agent = RuleBasedExperimentReadinessAgent()

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        retained = state.get("retained", [])
        objectives = state.get("objectives", [])
        seeds = state.get("seeds", [])
        config = state.get("config", {})
        ready_candidates = self._readiness_agent.score_state(
            generated=retained,
            objectives=objectives,
            seeds=seeds,
            config=config if isinstance(config, dict) else {},
        )
        labels: dict[str, int] = {}
        for candidate in ready_candidates:
            labels[candidate.readiness_bucket] = labels.get(candidate.readiness_bucket, 0) + 1
        state["experiment_readiness"] = {
            "label_counts": labels,
            "candidates": [
                candidate.model_dump(mode="json") for candidate in ready_candidates
            ],
            "default_bucket_scope": "expert_review",
            "human_review_required": True,
            "no_protocols": True,
        }
        return state


class ActiveLearningDesignAgent:
    name = "ActiveLearningDesignAgent"

    def __init__(self) -> None:
        self._planner = ActiveLearningDesignPlanner()

    def process(self, state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        retained = state.get("retained", [])
        objectives = state.get("objectives", [])
        experimental_results = state.get("experimental_results", [])
        config = state.get("config", {})
        strategy = (
            str(config.get("active_design_strategy", "balanced"))
            if isinstance(config, dict)
            else "balanced"
        )
        result = self._planner.plan_next_round(
            objectives=objectives,
            generated_candidates=retained,
            experimental_results=experimental_results,
            strategy=strategy,
        )
        state["active_learning_design"] = {
            "candidate_count": len(retained),
            "selected_strategy": result.selected_strategy,
            "suggested_focus": result.suggested_focus,
            "selected_candidates": [
                candidate.model_dump(mode="json")
                for candidate in result.selected_candidates
            ],
            "next_design_plan": result.next_design_plan.model_dump(mode="json"),
            "surrogate_metadata": result.surrogate_metadata,
            "warnings": result.warnings,
            "loop": "use exact feedback and oracle scores to guide the next generation round",
            "assay_results_fabricated": False,
            "surrogate_estimates_are_not_evidence": True,
            "no_protocols": True,
        }
        return state


class _ScientificDesignExecutable:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def execute(self, node: AgentNode, run: AgentGraphRun) -> AgentExecutionResult:
        state = dict(run.state)
        updated = self.agent.process(state)
        outputs = {key: updated[key] for key in node.outputs if key in updated}
        return AgentExecutionResult(outputs=outputs)


class ScientificDesignGraph:
    """Compatibility wrapper backed by the explicit AgentGraph runtime."""

    def __init__(self) -> None:
        self._agents = [
            ScientificDesignPlannerGraphNode(),
            DesignObjectiveAgent(),
            SeedAndScaffoldSelectionAgent(),
            GeneratorEnsembleAgent(),
            OracleScoringAgent(),
            MedicinalChemistryCriticAgent(),
            UncertaintyAndDiversityAgent(),
            ExperimentReadinessAgent(),
            ActiveLearningDesignAgent(),
        ]

    def run(self, state: MutableMapping[str, Any]) -> dict[str, Any]:
        run = AgentGraphRun(
            graph_run_id=str(state.get("graph_run_id") or "scientific-design-v1-1"),
            project_id=(
                state.get("project_id")
                if isinstance(state.get("project_id"), str)
                else None
            ),
            run_id=str(state.get("run_id") or "generation-run"),
            graph_version="1.1",
            nodes=self._nodes(),
            edges=self._edges(),
            state=dict(state),
            artifacts={},
            audit_events=[],
            status="pending",
            metadata={
                "workflow": "scientific_design",
                "claim_boundary": "computational planning and critique only",
            },
        )
        executed = AgentGraphExecutor(
            {
                agent.name: _ScientificDesignExecutable(agent)
                for agent in self._agents
            }
        ).execute(run)
        return {
            "runtime": "AgentGraph",
            "status": "completed" if executed.status == "succeeded" else executed.status,
            "graph_run_id": executed.graph_run_id,
            "graph_version": executed.graph_version,
            "executed_agents": [
                node.agent_name for node in executed.nodes if node.status == "succeeded"
            ],
            "node_metadata": [
                {
                    "node_id": node.node_id,
                    "agent": node.agent_name,
                    "agent_type": node.agent_type,
                    "inputs": list(node.inputs),
                    "outputs": list(node.outputs),
                    "status": node.status,
                }
                for node in executed.nodes
            ],
            "audit_events": executed.audit_events,
        }

    def _nodes(self) -> list[AgentNode]:
        declarations = [
            (
                "scientific-design-plan",
                "ScientificDesignPlannerAgent",
                [],
                ["scientific_design_plan"],
            ),
            (
                "design-objectives",
                "DesignObjectiveAgent",
                ["objectives"],
                ["design_objectives"],
            ),
            (
                "seed-scaffold-selection",
                "SeedAndScaffoldSelectionAgent",
                ["seeds"],
                ["seed_scaffold_selection"],
            ),
            (
                "generator-ensemble",
                "GeneratorEnsembleAgent",
                ["generated"],
                ["generator_ensemble"],
            ),
            ("oracle-scoring", "OracleScoringAgent", ["retained"], ["oracle_scoring"]),
            (
                "medchem-critic",
                "MedicinalChemistryCriticAgent",
                ["retained"],
                ["medicinal_chemistry_critique"],
            ),
            (
                "uncertainty-diversity",
                "UncertaintyAndDiversityAgent",
                ["retained"],
                ["uncertainty_and_diversity"],
            ),
            (
                "experiment-readiness",
                "ExperimentReadinessAgent",
                ["retained"],
                ["experiment_readiness"],
            ),
            (
                "active-learning-design",
                "ActiveLearningDesignAgent",
                ["retained"],
                ["active_learning_design"],
            ),
        ]
        return [
            AgentNode(
                node_id=node_id,
                agent_name=agent_name,
                agent_type="scientific_design",
                inputs=inputs,
                outputs=outputs,
                required_artifacts=[],
                optional_artifacts=[],
                status="pending",
                started_at=None,
                completed_at=None,
                metadata={"required": True},
            )
            for node_id, agent_name, inputs, outputs in declarations
        ]

    def _edges(self) -> list[AgentEdge]:
        return [
            AgentEdge(
                from_node_id="scientific-design-plan",
                to_node_id=node_id,
                artifact_key="scientific_design_plan",
                required=True,
                metadata={"edge_type": "planning_context"},
            )
            for node_id in [
                "design-objectives",
                "seed-scaffold-selection",
                "generator-ensemble",
                "oracle-scoring",
                "medchem-critic",
                "uncertainty-diversity",
                "experiment-readiness",
                "active-learning-design",
            ]
        ]


def scientific_design_graph() -> ScientificDesignGraph:
    return ScientificDesignGraph()
