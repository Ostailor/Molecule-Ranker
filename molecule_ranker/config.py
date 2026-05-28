from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

DEFAULT_GENERATION_ELEMENTS = ["C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"]


class RankerConfig(BaseModel):
    results_dir: Path = Path("results")
    cache_dir: Path = Path(".cache/molecule-ranker")
    use_cache: bool = True
    cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)
    data_source: str = "public_adapters"
    default_top: int = Field(default=20, ge=1)
    default_target_limit: int = Field(default=20, ge=1)
    target_source_limit: int = Field(default=100, ge=2)
    max_molecules_per_target: int = Field(default=10, ge=1)
    max_activity_records_per_target: int = Field(default=10, ge=1)
    max_indications_per_molecule: int = Field(default=20, ge=1)
    max_warnings_per_molecule: int = Field(default=20, ge=1)
    enable_literature: bool = True
    strict_literature: bool = False
    literature_sources: list[str] = Field(default_factory=lambda: ["pubmed"])
    enable_openalex_enrichment: bool = True
    max_literature_queries: int = Field(default=100, ge=1)
    max_papers_per_query: int = Field(default=10, ge=1)
    max_targets_for_literature: int = Field(default=10, ge=1)
    max_candidates_for_literature: int = Field(default=20, ge=1)
    ncbi_tool: str = "molecule-ranker"
    ncbi_email: str | None = None
    ncbi_api_key: str | None = None
    literature_request_timeout_seconds: float = Field(default=20.0, gt=0)
    literature_max_retries: int = Field(default=3, ge=0)
    literature_cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)
    max_literature_queries_per_candidate: int = Field(default=3, ge=1)
    max_literature_results_per_query: int = Field(default=5, ge=1)
    literature_failure_policy: str = "skip"
    enable_openalex_metadata: bool = False
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    strict_enrichment: bool = False
    allow_cached_real_data: bool = False
    enable_generation: bool = False
    enable_novel_generation: bool = False
    strict_generation: bool = False
    include_generated_in_main_ranking: bool = False
    generation_method: str = "generator_ensemble"
    enabled_generators: list[str] | None = None
    disabled_generators: list[str] = Field(default_factory=list)
    generator_budget_weights: dict[str, float] = Field(default_factory=dict)
    generation_random_seed: int | None = None
    max_seed_molecules: int = Field(default=20, ge=1)
    max_generation_objectives: int = Field(default=10, ge=1)
    generated_per_objective: int = Field(default=50, ge=0)
    max_generated_before_filtering: int = Field(default=1000, ge=1)
    max_retained_generated: int = Field(default=50, ge=1)
    max_generation_rounds: int = Field(default=2, ge=1)
    max_mutations_per_child: int = Field(default=4, ge=1)
    enable_crossover: bool = True
    min_seed_score: float = Field(default=0.35, ge=0.0, le=1.0)
    min_seed_target_relevance: float = Field(default=0.25, ge=0.0, le=1.0)
    min_target_relevance_for_generation: float = Field(default=0.25, ge=0.0, le=1.0)
    duplicate_similarity_threshold: float = Field(default=0.98, ge=0.0, le=1.0)
    near_duplicate_similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    distant_similarity_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    reject_distant_generated: bool = True
    reject_basic_alerts: bool = False
    enable_structure_filtering: bool = False
    filter_developability_failures: bool = False
    min_developability_score: float = Field(default=0.25, ge=0.0, le=1.0)
    enable_developability: bool = True
    strict_developability: bool = False
    assess_existing_molecules: bool = True
    assess_generated_molecules: bool = True
    developability_filter_mode: str = "filter_generated_only"
    reject_critical_alerts: bool = True
    reject_high_toxicity_risk: bool = False
    alert_mode: str = "deprioritize"
    enable_rule_based_admet: bool = True
    enable_local_admet_models: bool = False
    allow_rule_based_admet_fallback: bool = True
    enable_synthesizability: bool = True
    enable_structure_retrieval: bool = False
    require_developability_for_generated: bool = True
    enable_docking: bool = False
    strict_structure_mode: bool = False
    write_docking_artifacts: bool = False
    max_structures_per_target: int = Field(default=5, ge=1)
    max_docked_molecules: int = Field(default=20, ge=0)
    enable_tdc_benchmark: bool = False
    tdc_data_dir: Path = Path(".cache/molecule-ranker/tdc")
    allowed_generation_elements: list[str] = Field(
        default_factory=lambda: list(DEFAULT_GENERATION_ELEMENTS)
    )
    generated_candidate_limit: int | None = Field(default=None, ge=1)
    generation_attempt_budget: int | None = Field(default=None, ge=1)
    near_identical_similarity_threshold: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
    )
    enable_review_workflow: bool = False
    review_db_path: Path = Path(".review/molecule-ranker-review.sqlite")
    reviewer_id: str | None = None
    reviewer_name: str | None = None
    reviewer_role: str | None = None
    max_review_items: int = Field(default=100, ge=1)
    include_generated_in_review: bool = True
    generated_high_priority_allowed: bool = False
    review_priority_policy: str = "conservative"
    enable_feedback_prior: bool = False
    feedback_db_path: Path = Path(".review/molecule-ranker-feedback.sqlite")
    feedback_weight: float = Field(default=0.05, ge=0.0, le=0.25)
    require_same_disease_for_feedback: bool = True
    generate_review_dashboard: bool = False
    review_dashboard_dir: Path | None = None
    enable_experimental_evidence: bool = False
    experimental_db_path: Path = Path(".review/molecule-ranker-experiments.sqlite")
    experimental_result_source_filter: str | list[str] | None = None
    require_qc_passed_for_score: bool = True
    include_inconclusive_results: bool = True
    strict_experimental_linking: bool = True
    enable_codex_backbone: bool = False
    strict_codex_backbone: bool = False
    codex_tasks: list[str] = Field(
        default_factory=lambda: [
            "summarize_run",
            "explain_top_candidates",
            "draft_review_questions",
            "plan_followup_run",
        ]
    )
    codex_store_transcripts: bool = True
    codex_max_tasks_per_run: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def sync_generation_aliases(self) -> RankerConfig:
        if self.enable_novel_generation:
            self.enable_generation = True
        if self.generated_candidate_limit is not None:
            self.max_retained_generated = self.generated_candidate_limit
        if self.generation_attempt_budget is not None:
            self.max_generated_before_filtering = self.generation_attempt_budget
        if self.near_identical_similarity_threshold is not None:
            self.near_duplicate_similarity_threshold = self.near_identical_similarity_threshold
        return self

    def trace_metadata(self) -> dict[str, Any]:
        metadata = self.model_dump(mode="json")
        if metadata.get("ncbi_api_key"):
            metadata["ncbi_api_key"] = "***"
        return metadata

    def runtime_agent_config(self, *, top: int, results_dir: Path) -> dict[str, Any]:
        trace_metadata = {
            **self.trace_metadata(),
            "default_top": top,
            "results_dir": str(results_dir),
        }
        return {
            "top": top,
            "results_dir": str(results_dir),
            "default_target_limit": self.default_target_limit,
            "target_source_limit": self.target_source_limit,
            "max_molecules_per_target": self.max_molecules_per_target,
            "max_activity_records_per_target": self.max_activity_records_per_target,
            "max_indications_per_molecule": self.max_indications_per_molecule,
            "max_warnings_per_molecule": self.max_warnings_per_molecule,
            "max_literature_queries": self.max_literature_queries,
            "max_papers_per_query": self.max_papers_per_query,
            "max_targets_for_literature": self.max_targets_for_literature,
            "max_candidates_for_literature": self.max_candidates_for_literature,
            "max_literature_queries_per_candidate": self.max_literature_queries_per_candidate,
            "max_literature_results_per_query": self.max_literature_results_per_query,
            "enable_literature": self.enable_literature,
            "strict_literature": self.strict_literature,
            "literature_sources": list(self.literature_sources),
            "literature_failure_policy": (
                "fail" if self.strict_literature else self.literature_failure_policy
            ),
            "enable_openalex_enrichment": self.enable_openalex_enrichment,
            "enable_openalex_metadata": self.enable_openalex_metadata,
            "ncbi_tool": self.ncbi_tool,
            "ncbi_email": self.ncbi_email,
            "literature_request_timeout_seconds": self.literature_request_timeout_seconds,
            "literature_max_retries": self.literature_max_retries,
            "literature_cache_ttl_seconds": self.literature_cache_ttl_seconds,
            "strict_enrichment": self.strict_enrichment,
            "enable_generation": self.enable_generation,
            "enable_novel_generation": self.enable_generation,
            "strict_generation": self.strict_generation,
            "include_generated_in_main_ranking": self.include_generated_in_main_ranking,
            "generation_method": self.generation_method,
            "enabled_generators": self.enabled_generators,
            "disabled_generators": list(self.disabled_generators),
            "generator_budget_weights": dict(self.generator_budget_weights),
            "generation_random_seed": self.generation_random_seed,
            "max_seed_molecules": self.max_seed_molecules,
            "max_generation_objectives": self.max_generation_objectives,
            "generated_per_objective": self.generated_per_objective,
            "max_generated_before_filtering": self.max_generated_before_filtering,
            "max_retained_generated": self.max_retained_generated,
            "max_generation_rounds": self.max_generation_rounds,
            "max_mutations_per_child": self.max_mutations_per_child,
            "enable_crossover": self.enable_crossover,
            "min_seed_score": self.min_seed_score,
            "min_seed_target_relevance": self.min_seed_target_relevance,
            "min_target_relevance_for_generation": (self.min_target_relevance_for_generation),
            "duplicate_similarity_threshold": self.duplicate_similarity_threshold,
            "near_duplicate_similarity_threshold": self.near_duplicate_similarity_threshold,
            "distant_similarity_threshold": self.distant_similarity_threshold,
            "reject_distant_generated": self.reject_distant_generated,
            "reject_distant_generated_molecules": self.reject_distant_generated,
            "reject_basic_alerts": self.reject_basic_alerts,
            "enable_structure_filtering": self.enable_structure_filtering,
            "filter_developability_failures": self.filter_developability_failures,
            "min_developability_score": self.min_developability_score,
            "enable_developability": self.enable_developability,
            "strict_developability": self.strict_developability,
            "assess_existing_molecules": self.assess_existing_molecules,
            "assess_generated_molecules": self.assess_generated_molecules,
            "developability_filter_mode": self.developability_filter_mode,
            "reject_critical_alerts": self.reject_critical_alerts,
            "reject_high_toxicity_risk": self.reject_high_toxicity_risk,
            "alert_mode": self.alert_mode,
            "enable_rule_based_admet": self.enable_rule_based_admet,
            "enable_local_admet_models": self.enable_local_admet_models,
            "allow_rule_based_admet_fallback": self.allow_rule_based_admet_fallback,
            "enable_synthesizability": self.enable_synthesizability,
            "enable_structure_retrieval": self.enable_structure_retrieval,
            "require_developability_for_generated": (
                self.require_developability_for_generated
            ),
            "enable_docking": self.enable_docking,
            "strict_structure_mode": self.strict_structure_mode,
            "write_docking_artifacts": self.write_docking_artifacts,
            "max_structures_per_target": self.max_structures_per_target,
            "max_docked_molecules": self.max_docked_molecules,
            "enable_tdc_benchmark": self.enable_tdc_benchmark,
            "tdc_data_dir": str(self.tdc_data_dir),
            "basic_alerts_warning_only": not self.reject_basic_alerts,
            "allowed_generation_elements": list(self.allowed_generation_elements),
            "generated_candidate_limit": self.max_retained_generated,
            "generation_attempt_budget": self.max_generated_before_filtering,
            "near_identical_similarity_threshold": self.near_duplicate_similarity_threshold,
            "enable_review_workflow": self.enable_review_workflow,
            "review_db_path": str(self.review_db_path),
            "reviewer_id": self.reviewer_id,
            "reviewer_name": self.reviewer_name,
            "reviewer_role": self.reviewer_role,
            "max_review_items": self.max_review_items,
            "include_generated_in_review": self.include_generated_in_review,
            "generated_high_priority_allowed": self.generated_high_priority_allowed,
            "review_priority_policy": self.review_priority_policy,
            "enable_feedback_prior": self.enable_feedback_prior,
            "feedback_db_path": str(self.feedback_db_path),
            "feedback_weight": self.feedback_weight,
            "require_same_disease_for_feedback": self.require_same_disease_for_feedback,
            "generate_review_dashboard": self.generate_review_dashboard,
            "review_dashboard_dir": (
                str(self.review_dashboard_dir) if self.review_dashboard_dir is not None else None
            ),
            "enable_experimental_evidence": self.enable_experimental_evidence,
            "experimental_db_path": str(self.experimental_db_path),
            "experimental_result_source_filter": self.experimental_result_source_filter,
            "require_qc_passed_for_score": self.require_qc_passed_for_score,
            "include_inconclusive_results": self.include_inconclusive_results,
            "strict_experimental_linking": self.strict_experimental_linking,
            "enable_codex_backbone": self.enable_codex_backbone,
            "strict_codex_backbone": self.strict_codex_backbone,
            "codex_tasks": list(self.codex_tasks),
            "codex_store_transcripts": self.codex_store_transcripts,
            "codex_max_tasks_per_run": self.codex_max_tasks_per_run,
            "ranker_config": trace_metadata,
        }
