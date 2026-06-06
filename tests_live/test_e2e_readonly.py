from __future__ import annotations

import json
import os
import re

import pytest

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.openalex_adapter import OpenAlexAdapter
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.data_sources.pubmed_adapter import PubMedAdapter
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import EndToEndWorkflowRunner, WorkflowRunRequest

pytestmark = [
    pytest.mark.live,
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("MOLECULE_RANKER_RUN_LIVE") != "1",
        reason="Set MOLECULE_RANKER_RUN_LIVE=1 to run live network smoke tests.",
    ),
]

FORBIDDEN_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmedical advice\b", re.I),
    re.compile(r"\bpatient treatment\b", re.I),
    re.compile(r"\bdosing\b", re.I),
    re.compile(r"\blab\s+protocols?\b", re.I),
    re.compile(r"\bsynthesis\s+instructions?\b", re.I),
    re.compile(r"\bproven\s+safety\b", re.I),
    re.compile(r"\befficacy\b", re.I),
)


def test_live_e2e_public_source_health_checks() -> None:
    adapters = [
        OpenTargetsAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        ChEMBLAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        PubChemAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
        OpenAlexAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25),
    ]

    statuses = [adapter.health_check(timeout_seconds=10) for adapter in adapters]

    assert {status.source_name for status in statuses} == {
        "Open Targets",
        "ChEMBL",
        "PubChem",
        "PubMed",
        "OpenAlex",
    }
    for status in statuses:
        assert status.ok, f"{status.source_name} health failed: {status.error}"
        assert status.endpoint.startswith("https://")
        assert status.checked_at.tzinfo is not None
        assert status.latency_ms is None or status.latency_ms >= 0


def test_live_readonly_minimal_disease_to_ranked_candidates_bundle() -> None:
    result = EndToEndWorkflowRunner().run(
        WorkflowRunRequest(
            workflow_type="disease_to_ranked_candidates",
            mode="read_only_live",
            disease_name="Parkinson disease",
            project_id="live-readonly-smoke",
            requested_by="live-smoke",
            requested_external_write=False,
            metadata={
                "live_safe_smoke": True,
                "limit_policy": {
                    "max_targets": 3,
                    "max_molecules_per_target": 1,
                    "max_literature_records": 2,
                },
            },
        )
    )
    validation = EndToEndWorkflowValidator().validate_run_result(result)

    assert result.workflow.mode == "read_only_live"
    assert result.workflow.status == "succeeded"
    assert validation.passed, validation.findings
    assert validation.required_artifacts_present is True
    assert validation.artifact_contracts_valid is True
    assert validation.lineage_complete is True
    assert result.bundle is not None
    assert result.bundle.workflow_id == result.workflow.workflow_id
    assert result.bundle.key_artifact_ids
    assert set(result.bundle.key_artifact_ids) == {
        artifact_id
        for step in result.steps
        if step.status == "succeeded"
        for artifact_id in step.output_artifact_ids
    }

    assert result.external_writes_performed == 0
    assert result.planned_external_writes == 0
    assert result.bundle.integration_summary["external_writes_performed"] == 0
    assert not any(step.metadata.get("external_write") for step in result.steps)
    assert not any(step.metadata.get("destructive_action") for step in result.steps)
    assert not any(event.get("to") == "awaiting_approval" for event in result.audit_events)

    assert all(step.step_type != "generation" for step in result.steps)
    assert all(step.step_type != "campaign_planning" for step in result.steps)
    generated_advancements = result.bundle.generated_summary.get(
        "generated_molecules_advanced_without_review",
        0,
    )
    assert generated_advancements == 0
    assert result.bundle.generated_summary.get("advanced_without_review") is not True
    assert result.bundle.campaign_summary.get("campaign_activated") is not True
    assert result.bundle.metadata.get("stage_gate_approval_id") is None

    payload = json.dumps(result.bundle.model_dump(mode="json"), sort_keys=True)
    assert not any(pattern.search(payload) for pattern in FORBIDDEN_CLAIM_PATTERNS)
    assert "not scientific evidence" in " ".join(result.bundle.limitations).lower()
