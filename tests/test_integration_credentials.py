from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.integrations.credentials import (
    CredentialError,
    CredentialResolver,
    create_credential_reference,
    delete_credential_reference,
    list_credentials_redacted,
    redact_secret_values,
    resolve_credential,
)


def test_env_var_secret_resolution_keeps_value_in_memory_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BENCHLING_TEST_TOKEN", "benchling-secret-value")
    credential = create_credential_reference(
        external_system_id="ext-benchling",
        credential_type="api_key",
        secret_ref="env:BENCHLING_TEST_TOKEN",
        root_dir=tmp_path,
        credential_id="cred-env",
    )

    assert credential.secret_ref == "env:BENCHLING_TEST_TOKEN"
    assert resolve_credential("cred-env", root_dir=tmp_path) == "benchling-secret-value"

    registry_text = (tmp_path / ".molecule-ranker" / "integration_credentials.json").read_text()
    assert "benchling-secret-value" not in registry_text


def test_missing_env_secret_fails_clearly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MISSING_INTEGRATION_TOKEN", raising=False)
    create_credential_reference(
        external_system_id="ext-rest",
        credential_type="bearer_token",
        secret_ref="env:MISSING_INTEGRATION_TOKEN",
        root_dir=tmp_path,
        credential_id="cred-missing",
    )

    result = CredentialResolver(root_dir=tmp_path).validate_credential("cred-missing")

    assert result["ok"] is False
    assert "MISSING_INTEGRATION_TOKEN" in result["message"]


def test_secret_redaction_covers_env_values_and_literal_tokens(monkeypatch) -> None:
    monkeypatch.setenv("PRIVATE_API_TOKEN", "super-secret-token-value")

    redacted = redact_secret_values(
        "token=super-secret-token-value api_key=sk-secretsecretsecretsecret"
    )

    assert "super-secret-token-value" not in redacted
    assert "sk-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_list_credentials_never_shows_secret_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WAREHOUSE_PASSWORD", "warehouse-password-value")
    create_credential_reference(
        external_system_id="ext-warehouse",
        credential_type="database_password",
        secret_ref="env:WAREHOUSE_PASSWORD",
        root_dir=tmp_path,
        credential_id="cred-warehouse",
    )

    listed = list_credentials_redacted(root_dir=tmp_path)
    payload = json.dumps(listed, sort_keys=True)

    assert "warehouse-password-value" not in payload
    assert listed[0]["secret_ref"] == "env:WAREHOUSE_PASSWORD"


def test_delete_writes_revocation_metadata_and_redacted_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DELETE_TOKEN", "delete-secret-value")
    create_credential_reference(
        external_system_id="ext-rest",
        credential_type="api_key",
        secret_ref="env:DELETE_TOKEN",
        root_dir=tmp_path,
        credential_id="cred-delete",
    )

    deleted = delete_credential_reference(
        "cred-delete",
        root_dir=tmp_path,
        reason="rotated delete-secret-value",
    )
    registry = json.loads(
        (tmp_path / ".molecule-ranker" / "integration_credentials.json").read_text()
    )

    assert deleted["metadata"]["status"] == "revoked"
    assert "delete-secret-value" not in json.dumps(registry, sort_keys=True)
    assert registry["audit_events"][-1]["event_type"] == "integration_credential_revoked"

    try:
        resolve_credential("cred-delete", root_dir=tmp_path)
    except CredentialError as exc:
        assert "revoked" in str(exc)
    else:
        raise AssertionError("revoked credential unexpectedly resolved")


def test_integration_credential_cli_create_list_test_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLI_INTEGRATION_TOKEN", "cli-secret-value")
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "integration",
            "credential",
            "create",
            "--root",
            str(tmp_path),
            "--external-system-id",
            "ext-cli",
            "--credential-type",
            "api_key",
            "--secret-ref",
            "env:CLI_INTEGRATION_TOKEN",
            "--credential-id",
            "cred-cli",
            "--json",
        ],
    )
    listed = runner.invoke(
        app,
        ["integration", "credential", "list", "--root", str(tmp_path), "--json"],
    )
    tested = runner.invoke(
        app,
        ["integration", "credential", "test", "cred-cli", "--root", str(tmp_path), "--json"],
    )
    deleted = runner.invoke(
        app,
        ["integration", "credential", "delete", "cred-cli", "--root", str(tmp_path), "--json"],
    )

    assert created.exit_code == 0, created.stdout
    assert listed.exit_code == 0, listed.stdout
    assert tested.exit_code == 0, tested.stdout
    assert deleted.exit_code == 0, deleted.stdout
    combined = created.stdout + listed.stdout + tested.stdout + deleted.stdout
    assert "cli-secret-value" not in combined
    assert json.loads(tested.stdout)["ok"] is True
