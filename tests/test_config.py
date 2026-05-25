from __future__ import annotations

from molecule_ranker.config import RankerConfig


def test_ranker_config_defaults_are_sensible_for_first_real_run():
    config = RankerConfig()

    assert config.results_dir.name == "results"
    assert config.cache_dir.as_posix() == ".cache/molecule-ranker"
    assert config.use_cache is True
    assert config.allow_cached_real_data is False
    assert config.cache_ttl_seconds == 24 * 60 * 60
    assert config.default_top == 20
    assert config.default_target_limit > 1
    assert config.target_source_limit >= config.default_target_limit
    assert config.max_molecules_per_target >= 1
    assert config.max_activity_records_per_target >= 1
    assert config.max_indications_per_molecule >= 1
    assert config.max_warnings_per_molecule >= 1
    assert config.enable_literature is True
    assert config.strict_literature is False
    assert config.literature_sources == ["pubmed"]
    assert config.enable_openalex_enrichment is True
    assert config.max_literature_queries == 100
    assert config.max_papers_per_query == 10
    assert config.max_targets_for_literature == 10
    assert config.max_candidates_for_literature == 20
    assert config.ncbi_tool == "molecule-ranker"
    assert config.ncbi_email is None
    assert config.ncbi_api_key is None
    assert config.literature_request_timeout_seconds > 0
    assert config.literature_max_retries >= 0
    assert config.literature_cache_ttl_seconds >= 1
    assert config.request_timeout_seconds > 0
    assert config.max_retries >= 1
    assert config.retry_backoff_seconds >= 0
    assert config.strict_enrichment is False
    assert config.enable_generation is False
    assert config.strict_generation is False
    assert config.include_generated_in_main_ranking is False
    assert config.generation_method == "selfies_mutation"
    assert config.generation_random_seed is None
    assert config.max_seed_molecules == 20
    assert config.max_generation_objectives == 10
    assert config.generated_per_objective == 50
    assert config.max_generated_before_filtering == 1000
    assert config.max_retained_generated == 50
    assert config.max_generation_rounds == 2
    assert config.max_mutations_per_child == 4
    assert config.enable_crossover is True
    assert config.min_seed_score == 0.35
    assert config.min_seed_target_relevance == 0.25
    assert config.min_target_relevance_for_generation == 0.25
    assert config.duplicate_similarity_threshold == 0.98
    assert config.near_duplicate_similarity_threshold == 0.90
    assert config.distant_similarity_threshold == 0.25
    assert config.reject_distant_generated is True
    assert config.reject_basic_alerts is False
    assert config.enable_developability is True
    assert config.strict_developability is False
    assert config.assess_existing_molecules is True
    assert config.assess_generated_molecules is True
    assert config.developability_filter_mode == "filter_generated_only"
    assert config.reject_critical_alerts is True
    assert config.reject_high_toxicity_risk is False
    assert config.alert_mode == "deprioritize"
    assert config.enable_rule_based_admet is True
    assert config.enable_local_admet_models is False
    assert config.allow_rule_based_admet_fallback is True
    assert config.enable_synthesizability is True
    assert config.enable_structure_retrieval is False
    assert config.enable_docking is False
    assert config.strict_structure_mode is False
    assert config.write_docking_artifacts is False
    assert config.max_structures_per_target == 5
    assert config.max_docked_molecules == 20
    assert config.enable_tdc_benchmark is False
    assert config.tdc_data_dir.as_posix() == ".cache/molecule-ranker/tdc"
    assert config.allowed_generation_elements == [
        "C",
        "H",
        "N",
        "O",
        "S",
        "P",
        "F",
        "Cl",
        "Br",
        "I",
    ]


def test_ranker_config_trace_metadata_is_json_serializable():
    metadata = RankerConfig().trace_metadata()

    assert metadata["results_dir"] == "results"
    assert metadata["cache_dir"] == ".cache/molecule-ranker"
    assert metadata["default_target_limit"] > 1
    assert metadata["enable_literature"] is True
    assert metadata["literature_sources"] == ["pubmed"]
    assert metadata["enable_generation"] is False
    assert metadata["enable_developability"] is True
    assert metadata["enable_docking"] is False
    assert metadata["enable_tdc_benchmark"] is False
    assert metadata["tdc_data_dir"] == ".cache/molecule-ranker/tdc"
