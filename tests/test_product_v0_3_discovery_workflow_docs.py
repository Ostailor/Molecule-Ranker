from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "product" / "v0_3_discovery_workflow.md"
ENGINE_BOUNDARY = ROOT / "docs" / "product" / "v0_3_engine_boundary.md"
RELEASE_NOTES = ROOT / "docs" / "product" / "v0_3_release_notes.md"
RELEASE_TRACK = ROOT / "docs" / "product" / "release_track.md"


def test_v0_3_discovery_workflow_doc_exists_with_required_boundaries() -> None:
    text = DOC.read_text()

    for phrase in [
        "product_runs",
        "product_run_artifacts",
        "Default mode is `dry_run`",
        "`read_only_live` is disabled",
        "write_approved_live",
        "external writes",
        "raw AgentGraph exposure",
        "raw Codex transcripts",
        "not clinical\nvalidation",
        "No Stripe or paid subscriptions",
    ]:
        assert phrase in text


def test_release_track_marks_v0_3_discovery_workflow() -> None:
    text = RELEASE_TRACK.read_text()

    assert "## Release V0.3: Discovery Workflow From Web App To Engine" in text
    assert "Status: implemented as the Bounded Discovery Workflow release." in text
    assert "Reference: `docs/product/v0_3_discovery_workflow.md`" in text
    assert "Product-safe result artifacts are stored behind tenant-scoped APIs." in text


def test_v0_3_release_notes_cover_scope_and_disabled_features() -> None:
    text = RELEASE_NOTES.read_text()

    for section in ["## What Changed", "## Still Disabled", "## How To Test", "## Known Limitations"]:
        assert section in text

    for phrase in [
        "product_runs",
        "product_run_artifacts",
        "product-safe engine runner wrapper",
        "Updated product release version to `0.3.0`",
        "Stripe and paid subscriptions",
        "External writes and external integrations",
        "Raw AgentGraph, Codex transcript, trace, and log exposure",
    ]:
        assert phrase in text


def test_v0_3_engine_boundary_documents_product_safe_wrapper() -> None:
    text = ENGINE_BOUNDARY.read_text()

    for phrase in [
        "Dev V3.0",
        "product-safe engine wrapper",
        "Product APIs do not expose raw engine internals",
        "Product users see result bundles",
        "Admins may see redacted diagnostics",
        "Engine failures become safe product errors",
        "Engine artifacts are filtered before user exposure",
        "`molecule-ranker discover`",
        "`mocked`",
        "`dry_run`",
        "`read_only_live`",
        "`write_approved_live`",
        "External writes",
        "Antibody generation",
        "Generated hypotheses must use a low product-safe limit",
        "<organization_id>/<project_id>/<run_id>",
        "Result bundle JSON",
        "Result bundle Markdown",
        "Candidate summary",
        "Generated hypothesis summary",
        "Evidence summary",
        "Validation summary",
        "Redacted trace for admin-only diagnostics",
        "Raw AgentGraph state",
        "Raw Codex transcripts",
        "Raw tool logs",
        "Raw repair logs",
        "Raw governance internals",
        "Cache files",
        "Secrets",
        "External credential details",
        "queued -> running -> succeeded",
        "partially_succeeded",
        "production-grade background queue",
        "not billing-gated",
        "summary-level until V0.4",
    ]:
        assert phrase in text
