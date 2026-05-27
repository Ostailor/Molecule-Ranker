from __future__ import annotations

from pathlib import Path

import pytest

from molecule_ranker.integrations.connectors import GenericFileConnector
from molecule_ranker.integrations.connectors.base import ConnectorError
from molecule_ranker.integrations.schemas import ConnectorConfig


def test_generic_file_scan_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "assay.csv").write_text(_assay_csv())
    (inbox / ".hidden.csv").write_text(_assay_csv())

    connector = GenericFileConnector(_config(tmp_path))
    scanned = connector.scan_inbox()

    assert [item["file_name"] for item in scanned] == ["assay.csv"]
    assert len(scanned[0]["sha256"]) == 64
    assert scanned[0]["external_ref"].external_record_type == "file"


def test_generic_file_import_csv_assay_results(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "assay.csv").write_text(_assay_csv())
    connector = GenericFileConnector(_config(tmp_path))

    result = connector.import_file("assay.csv")

    assert result["status"] == "imported"
    assert result["import_result"].validation_report.valid_count == 1
    assert result["import_result"].results[0].candidate_id == "cand-1"


def test_generic_file_duplicate_skipped_by_hash(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "assay.csv").write_text(_assay_csv())
    connector = GenericFileConnector(_config(tmp_path))

    first = connector.import_file("assay.csv")
    second = connector.import_file("assay.csv")

    assert first["status"] == "imported"
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate_checksum"


def test_generic_file_path_traversal_blocked(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    connector = GenericFileConnector(_config(tmp_path))

    with pytest.raises(ConnectorError, match="Path traversal"):
        connector.import_file("../outside.csv")


def test_generic_file_dry_run_does_not_move_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    inbox.mkdir()
    (inbox / "assay.csv").write_text(_assay_csv())
    connector = GenericFileConnector(_config(tmp_path, move_after_processing=True))

    result = connector.import_file("assay.csv")

    assert result["status"] == "imported"
    assert (inbox / "assay.csv").exists()
    assert not (processed / "assay.csv").exists()


def _config(tmp_path: Path, *, move_after_processing: bool = False) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="generic-file-test",
        name="Generic file",
        provider="generic_csv_sftp",
        kind="csv_sftp",
        mode="dry_run",
        config={
            "root_dir": str(tmp_path),
            "inbox_dir": "inbox",
            "processed_dir": "processed",
            "failed_dir": "failed",
            "outbox_dir": "outbox",
            "move_after_processing": move_after_processing,
            "max_file_size_bytes": 1024 * 1024,
        },
    )


def _assay_csv() -> str:
    return (
        "experiment_id,assay_name,candidate_id,outcome,value,unit\n"
        "exp-1,Binding,cand-1,positive,12.3,nM\n"
    )
