from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.auth import generate_opaque_token
from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.security_audit import run_security_audit


def test_security_audit_catches_intentionally_bad_fixtures(tmp_path: Path) -> None:
    bad_fixture = {
        "users": [{"email": "bad@example.test", "password_hash": "Plaintext-password-1"}],
        "service_tokens": [{"token_hash": "mrs_plaintext_token", "token": "mrs_plaintext_token"}],
        "logs": [{"message": "api_key=sk-badbadbadbadbadbadbadbad"}],
        "artifacts": [
            {"path": "../outside.txt", "downloadable": True},
            {"path": ".env", "served": True},
            {"path": ".cache/payload.json", "downloadable": True},
            {"path": "export.zip", "contains_secret": True},
        ],
        "routes": [{"path": "/projects", "hosted": True, "auth_required": False}],
        "codex_jobs": [
            {"task_type": "summarize_project", "permission_required": "project:read"},
            {"allowed_artifact_paths": ["/etc/passwd"]},
        ],
        "webhooks": [{"path": "/webhooks/ext", "signature_required": False}],
        "sql": [{"query": "select * from assay_results where candidate_id = 'C1'"}],
        "file_connectors": [{"root_dir": "data", "path": "../secret.csv"}],
        "warehouse": [{"query": "select * from candidates where id = 'C1'", "allowlisted": False}],
        "audit_logs": [{"summary": "token=super-secret-token-value"}],
        "config_show": {"secret_key": "super-secret-config-value", "redacted": False},
    }
    (tmp_path / "security_bad_fixtures.json").write_text(json.dumps(bad_fixture))

    report = run_security_audit(root_dir=tmp_path)

    assert report.status == "fail"
    check_ids = {finding.check_id for finding in report.findings}
    assert {
        "password_hashes_not_plaintext",
        "service_tokens_not_recoverable",
        "api_keys_secrets_redacted",
        "env_files_never_served",
        "cache_files_not_downloadable",
        "artifact_path_traversal_blocked",
        "hosted_routes_require_auth",
        "codex_jobs_require_codex_permission",
        "codex_worker_scoped_files_only",
        "webhook_signatures_required",
        "sql_injection_protections_active",
        "file_connector_path_traversal_blocked",
        "warehouse_queries_parameterized_or_allowlisted",
        "export_packages_exclude_secrets",
        "audit_logs_do_not_include_secrets",
        "config_show_redacts_secrets",
    } <= check_ids
    assert (tmp_path / "security_audit.json").exists()
    assert (tmp_path / "security_audit.md").exists()


def test_security_audit_passes_clean_mocked_platform_config(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    database = PlatformDatabase(tmp_path, db_path=db_path)
    user = database.create_user(email="admin@example.test", password="Admin-password-1")
    service_token = generate_opaque_token(prefix="mrs")
    database.create_service_account_token(
        name="automation",
        token=service_token,
        user_id=user.user_id,
        created_by_user_id=user.user_id,
        scopes=["project:read"],
        metadata={"note": "safe metadata"},
    )
    database.write_audit(
        "security_test",
        actor_user_id=user.user_id,
        summary="Created token sk-clean-secret-value-1234567890",
        metadata={"api_key": "sk-clean-secret-value-1234567890"},
    )
    (tmp_path / "security_clean_fixtures.json").write_text(
        json.dumps(
            {
                "routes": [{"path": "/projects", "hosted": True, "auth_required": True}],
                "codex_jobs": [
                    {"task_type": "summarize_project", "permission_required": "codex:run"}
                ],
                "webhooks": [{"path": "/webhooks/ext", "signature_required": True}],
                "sql": [
                    {"query": "select * from assay_results where candidate_id = :candidate_id"}
                ],
                "warehouse": [
                    {
                        "query": "select * from candidates where id = :candidate_id",
                        "parameters": ["candidate_id"],
                    }
                ],
                "config_show": {"secret_key": "[REDACTED]", "redacted": True},
            }
        )
    )

    report = run_security_audit(root_dir=tmp_path, db_path=db_path)

    assert report.status == "pass"
    assert report.findings == []
    assert all(check.status == "pass" for check in report.checks)


def test_validate_security_cli_writes_reports(tmp_path: Path) -> None:
    (tmp_path / "security_clean_fixtures.json").write_text(
        json.dumps({"config_show": {"secret_key": "[REDACTED]", "redacted": True}})
    )

    result = CliRunner().invoke(app, ["validate", "security", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert (tmp_path / "security_audit.json").exists()
    assert (tmp_path / "security_audit.md").exists()
