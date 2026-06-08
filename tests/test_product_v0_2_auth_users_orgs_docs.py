from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "product" / "v0_2_auth_users_orgs.md"
RELEASE_NOTES = ROOT / "docs" / "product" / "v0_2_release_notes.md"
RELEASE_TRACK = ROOT / "docs" / "product" / "release_track.md"


def test_v0_2_auth_users_orgs_doc_exists_with_required_sections() -> None:
    text = DOC.read_text()

    required_sections = [
        "## V0.2 Scope",
        "## Supabase Setup Steps",
        "## Environment Variables",
        "## Schema And Migration Setup",
        "## RLS Policy Summary",
        "## Role Model",
        "## Permission Model",
        "## Protected Routes",
        "## Product API Auth Context",
        "## Tenant Isolation",
        "## Admin-Only Surfaces",
        "## Still Mock Or Placeholder",
        "## Moves To V0.3",
        "## Moves To V0.5",
        "## Security Checklist",
    ]

    for section in required_sections:
        assert section in text


def test_v0_2_auth_users_orgs_doc_preserves_security_boundaries() -> None:
    text = DOC.read_text()

    required_phrases = [
        "RLS is enabled and forced",
        "Service role key is never used in browser code",
        ".env.local is ignored",
        "Admin routes and APIs require owner/admin role",
        "Cross-org project, membership, feedback, and usage data is blocked",
        "Patient/PHI warnings are visible",
        "Research-use disclaimers are visible",
        "Product copy avoids clinical, lab, synthesis, and dosing claims",
        "Default CI uses offline tenant-isolation tests",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_release_track_marks_v0_2_auth_users_orgs_permissions() -> None:
    text = RELEASE_TRACK.read_text()

    assert "## Release V0.2: Auth, Users, Organizations, Permissions" in text
    assert "Status: implemented as the Auth, Users, Organizations, Permissions release." in text
    assert "Reference: `docs/product/v0_2_auth_users_orgs.md`" in text


def test_v0_2_release_notes_cover_status_testing_and_next_release() -> None:
    text = RELEASE_NOTES.read_text()
    normalized = " ".join(text.split())

    for section in [
        "## What Changed",
        "## What Remains Placeholder",
        "## How To Test",
        "## Known Limitations",
        "## Next Release: V0.3 Discovery Workflow Connection",
    ]:
        assert section in text

    assert "Stripe is not implemented" in text
    assert "Live engine execution" in text
    assert "npm test" in text
    assert "python -m pytest" in text
    assert "must not be required in default CI" in normalized
    assert "V0.3 connects the discovery workflow" in text
