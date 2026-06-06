from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.v2_package import (
    REQUIRED_V2_VALIDATION_REPORTS,
    generate_v2_validation_package,
)


def test_v2_validation_package_generated_with_valid_manifest(tmp_path: Path) -> None:
    root = _workspace_with_secret_files(tmp_path)
    output_dir = tmp_path / "validation_package"

    result = generate_v2_validation_package(output_dir=output_dir, root_dir=root)

    manifest_path = output_dir / "validation_package_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert result.status == "pass"
    assert manifest["status"] == "pass"
    assert manifest["scope"] == "software_platform_validation"
    assert manifest["clinical_or_regulatory_status"] == "not_clinical_not_regulatory_approval"
    assert manifest["version"] == "2.9.0"
    assert manifest["git_commit"]
    assert manifest["dependency_lock_hash"]
    assert set(REQUIRED_V2_VALIDATION_REPORTS).issubset(manifest["reports"])
    assert (output_dir / "known_limitations.md").exists()


def test_v2_validation_package_includes_required_reports(tmp_path: Path) -> None:
    output_dir = tmp_path / "validation_package"

    generate_v2_validation_package(output_dir=output_dir, root_dir=tmp_path)

    for report_name in REQUIRED_V2_VALIDATION_REPORTS:
        assert (output_dir / report_name).exists(), report_name
    limitations = (output_dir / "known_limitations.md").read_text()
    assert "internal research use only" in limitations
    assert "not a clinical product" in limitations
    assert "not regulatory approval" in limitations


def test_v2_validation_package_excludes_secrets_cache_and_full_text(tmp_path: Path) -> None:
    root = _workspace_with_secret_files(tmp_path)
    output_dir = tmp_path / "validation_package"

    generate_v2_validation_package(output_dir=output_dir, root_dir=root)

    packaged_text = "\n".join(
        path.read_text(errors="ignore")
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".txt"}
    )
    assert "super-secret-value" not in packaged_text
    assert "cache-secret-value" not in packaged_text
    assert "full copyrighted article body" not in packaged_text.lower()
    assert ".cache" not in {part for path in output_dir.rglob("*") for part in path.parts}


def test_v2_validation_package_cli_writes_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "validation_package.zip"

    result = CliRunner().invoke(
        app,
        ["validate", "v2-package", "--root", str(tmp_path), "--zip", str(zip_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["zip_path"] == str(zip_path.resolve())
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "validation_package_manifest.json" in names
    assert set(REQUIRED_V2_VALIDATION_REPORTS).issubset(names)


def _workspace_with_secret_files(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".env").write_text("API_KEY=super-secret-value\n")
    (root / ".cache").mkdir()
    (root / ".cache" / "cached-token.txt").write_text("cache-secret-value\n")
    (root / "article.txt").write_text("full copyrighted article body should not be copied\n")
    return root
