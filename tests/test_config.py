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


def test_ranker_config_trace_metadata_is_json_serializable():
    metadata = RankerConfig().trace_metadata()

    assert metadata["results_dir"] == "results"
    assert metadata["cache_dir"] == ".cache/molecule-ranker"
    assert metadata["default_target_limit"] > 1
    assert metadata["enable_literature"] is True
    assert metadata["literature_sources"] == ["pubmed"]
