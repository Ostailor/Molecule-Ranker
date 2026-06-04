from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.support_bundle import create_support_bundle, redact_file


def test_support_bundle_created_with_manifest(tmp_path: Path) -> None:
    root = _pilot_fixture_root(tmp_path)
    output = tmp_path / "support_bundle.zip"

    bundle = create_support_bundle(root_dir=root, output_path=output)

    assert output.exists()
    assert bundle.output_path == output
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "readiness_report.json" in names
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["version"] == "2.2.0"
    assert manifest["excluded"]["cache_payloads"] is True
    assert manifest["artifact_manifest_hashes"]


def test_support_bundle_redacts_secrets(tmp_path: Path) -> None:
    root = _pilot_fixture_root(tmp_path)
    (root / "logs" / "pilot.log").write_text(
        "Authorization: bearer-token-value\napi_key=secret-token-value\n",
        encoding="utf-8",
    )

    output = create_support_bundle(root_dir=root, output_path=tmp_path / "bundle.zip").output_path

    with zipfile.ZipFile(output) as archive:
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
            if name.endswith((".json", ".log", ".txt"))
        )
    assert "secret-token-value" not in combined
    assert "bearer-token-value" not in combined
    assert "[REDACTED]" in combined


def test_support_bundle_excludes_cache_files(tmp_path: Path) -> None:
    root = _pilot_fixture_root(tmp_path)
    cache_file = root / ".cache" / "payload.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"api_key":"secret-token-value"}', encoding="utf-8")

    output = create_support_bundle(root_dir=root, output_path=tmp_path / "bundle.zip").output_path

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert all(".cache" not in name for name in names)


def test_support_redact_cli(tmp_path: Path) -> None:
    source = tmp_path / "log.txt"
    target = tmp_path / "redacted.log"
    source.write_text("password=secret-token-value\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["support", "redact", "--input", str(source), "--output", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert "secret-token-value" not in target.read_text(encoding="utf-8")
    assert "[REDACTED]" in target.read_text(encoding="utf-8")


def test_support_bundle_cli(tmp_path: Path) -> None:
    root = _pilot_fixture_root(tmp_path)
    output = tmp_path / "support_bundle.zip"

    result = CliRunner().invoke(
        app,
        ["support", "bundle", "--root", str(root), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        assert "manifest.json" in archive.namelist()


def test_redact_file_writes_redacted_output(tmp_path: Path) -> None:
    source = tmp_path / "trace.txt"
    target = tmp_path / "trace.redacted.txt"
    source.write_text("service_token=secret-token-value\n", encoding="utf-8")

    redact_file(source, target)

    text = target.read_text(encoding="utf-8")
    assert "secret-token-value" not in text
    assert "[REDACTED]" in text


def _pilot_fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "pilot-root"
    (root / "results").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "traces").mkdir()
    (root / "results" / "candidates.json").write_text(
        json.dumps(
            {
                "artifact_contract_version": "1.9",
                "success": True,
                "targets": [],
                "candidates": [],
                "summary": "synthetic fixture",
            }
        ),
        encoding="utf-8",
    )
    (root / "validation_report.json").write_text('{"status":"pass"}', encoding="utf-8")
    (root / "security_audit_summary.json").write_text('{"status":"pass"}', encoding="utf-8")
    (root / "guardrail_benchmark_summary.json").write_text('{"status":"pass"}', encoding="utf-8")
    (root / "performance_report.json").write_text(
        '{"workflow":"golden","summary":{"total_duration_seconds":1.0}}',
        encoding="utf-8",
    )
    (root / "logs" / "pilot.log").write_text("startup ok\n", encoding="utf-8")
    (root / "traces" / "request.trace").write_text("trace_id=req-1\n", encoding="utf-8")
    return root
