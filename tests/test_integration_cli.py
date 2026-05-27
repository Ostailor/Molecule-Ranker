from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_integration_system_create_and_list(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"

    created = runner.invoke(
        app,
        [
            "integration",
            "system",
            "create",
            "--name",
            "Benchling Dev",
            "--system-type",
            "eln",
            "--vendor",
            "benchling",
            "--base-url",
            "https://benchling.example",
            "--mode",
            "dry_run",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.stdout
    assert json.loads(created.stdout)["system"]["external_system_id"] == "ext-benchling-dev"

    listed = runner.invoke(
        app,
        [
            "integration",
            "system",
            "list",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert listed.exit_code == 0, listed.stdout
    systems = json.loads(listed.stdout)["systems"]
    assert [system["external_system_id"] for system in systems] == ["ext-benchling-dev"]


def test_integration_credential_create_redacts_env_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BENCHLING_CLI_TOKEN", "benchling-secret-value")
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "integration",
            "credential",
            "create",
            "--external-system-id",
            "ext-benchling-dev",
            "--credential-type",
            "api_key",
            "--secret-env-var",
            "BENCHLING_CLI_TOKEN",
            "--credential-id",
            "cred-cli-env",
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.stdout
    assert "benchling-secret-value" not in created.stdout

    listed = runner.invoke(
        app,
        ["integration", "credential", "list", "--root", str(tmp_path), "--json"],
    )
    assert listed.exit_code == 0, listed.stdout
    assert "benchling-secret-value" not in listed.stdout
    credentials = json.loads(listed.stdout)["credentials"]
    assert credentials[0]["secret_ref"] == "env:BENCHLING_CLI_TOKEN"


def test_integration_sync_dry_run(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"
    _create_system(runner, tmp_path, db_path, mode="dry_run")

    result = runner.invoke(
        app,
        [
            "integration",
            "sync",
            "run",
            "--external-system-id",
            "ext-generic-rest",
            "--direction",
            "import",
            "--object-type",
            "assay_results",
            "--project-id",
            "project-1",
            "--dry-run",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["sync_job"]["mode"] == "dry_run"
    assert payload["sync_job"]["status"] == "succeeded"
    assert payload["sync_job"]["warnings"]


def test_integration_sync_write_blocked_unless_enabled(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"
    _create_system(runner, tmp_path, db_path, mode="dry_run")

    blocked = runner.invoke(
        app,
        [
            "integration",
            "sync",
            "run",
            "--external-system-id",
            "ext-generic-rest",
            "--direction",
            "export",
            "--object-type",
            "review_dossiers",
            "--project-id",
            "project-1",
            "--write-enabled",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert blocked.exit_code == 1
    assert "write-enabled sync requires" in blocked.stderr


def test_integration_warehouse_export_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"
    _create_system(
        runner,
        tmp_path,
        db_path,
        name="Warehouse",
        system_type="data_warehouse",
        vendor="postgresql",
        mode="dry_run",
    )
    output_dir = tmp_path / "warehouse-export"

    result = runner.invoke(
        app,
        [
            "integration",
            "warehouse",
            "export",
            "--project-id",
            "project-1",
            "--external-system-id",
            "ext-warehouse",
            "--tables",
            "candidates,assay_results",
            "--format",
            "csv",
            "--dry-run",
            "--output-dir",
            str(output_dir),
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert [item["table"] for item in payload["exports"]] == [
        "mr_candidates",
        "mr_assay_results",
    ]
    assert (output_dir / "mr_candidates.csv").exists()
    assert (output_dir / "mr_assay_results.csv").exists()


def _create_system(
    runner: CliRunner,
    root: Path,
    db_path: Path,
    *,
    name: str = "Generic REST",
    system_type: str = "generic_rest",
    vendor: str = "generic",
    mode: str = "dry_run",
) -> None:
    result = runner.invoke(
        app,
        [
            "integration",
            "system",
            "create",
            "--name",
            name,
            "--system-type",
            system_type,
            "--vendor",
            vendor,
            "--mode",
            mode,
            "--root",
            str(root),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
