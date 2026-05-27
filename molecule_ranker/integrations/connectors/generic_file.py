from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from molecule_ranker.experimental.importers import import_assay_results
from molecule_ranker.experimental.schemas import AssayImportResult, AssayResult
from molecule_ranker.integrations.connectors.base import (
    AssayConnector,
    ConnectorCallRecorder,
    ConnectorError,
)
from molecule_ranker.integrations.schemas import ConnectorConfig, ExternalRecordRef

SUPPORTED_SUFFIXES = {".csv", ".json", ".jsonl", ".parquet"}
DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


class GenericFileConnector(AssayConnector):
    connector_name = "generic-file"
    provider = "generic_csv_sftp"
    system_type = "generic_file"
    capabilities = (
        "scan_inbox",
        "file_checksum_tracking",
        "csv_import",
        "json_import",
        "parquet_import_if_available",
        "idempotent_imports",
        "outbox_export",
        "manifest_generation",
        "sftp_placeholder",
    )
    limitations = AssayConnector.limitations + (
        "SFTP-style support is an interface placeholder; this module does not open SSH sessions.",
        "Only local or mounted folders under the configured root are read.",
        "Files are parsed as data only and are never executed.",
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
    ) -> None:
        effective_config = config
        if config.mode == "dry_run" and not config.allow_writes:
            effective_config = config.model_copy(update={"mode": "dry_run"})
        super().__init__(effective_config, recorder=recorder)
        self._seen_hashes: set[str] = set(self.config.config.get("processed_hashes") or [])

    def scan_inbox(self) -> list[dict[str, Any]]:
        return self._call("scan_inbox", self._scan_inbox)

    def import_file(self, file_name: str, *, object_type: str = "assay_result") -> dict[str, Any]:
        return self._call(
            "import_file",
            lambda: self._import_file(file_name, object_type=object_type),
        )

    def write_export_file(
        self,
        records: list[dict[str, Any]],
        *,
        file_name: str,
        file_format: str = "json",
    ) -> dict[str, Any]:
        return self._call(
            "write_export_file",
            lambda: self._write_export_file(records, file_name=file_name, file_format=file_format),
            write=True,
            payload=records,
        )

    def generate_manifest(self) -> dict[str, Any]:
        return self._call("generate_manifest", self._generate_manifest)

    def sftp_connect(self) -> None:
        raise ConnectorError("SFTP support is a V0.9 placeholder; no SSH sessions are opened.")

    def _list_assay_runs(self) -> list[dict[str, Any]]:
        return []

    def _get_assay_results(self, file_name: str | None = None) -> list[dict[str, Any]]:
        imports = self._import_assay_results(file_name=file_name)
        if isinstance(imports, AssayImportResult):
            results = imports.results
        else:
            results = imports
        return [_assay_result_record(result, self.config.connector_id) for result in results]

    def _import_assay_results(
        self,
        *,
        file_name: str | None = None,
    ) -> list[AssayResult] | AssayImportResult:
        if file_name is None:
            imported: list[AssayResult] = []
            for item in self._scan_inbox():
                result = self._import_file(str(item["file_name"]), object_type="assay_result")
                if result["status"] == "imported":
                    imported.extend(result["import_result"].results)
            return imported
        return self._import_file(file_name, object_type="assay_result")["import_result"]

    def _export_assay_results(self, results: list[AssayResult]) -> dict[str, Any]:
        rows = [result.model_dump(mode="json") for result in results]
        return self._write_export_file(rows, file_name="assay_results.json", file_format="json")

    def _scan_inbox(self) -> list[dict[str, Any]]:
        inbox = self._inbox_dir()
        if not inbox.exists():
            return []
        files: list[dict[str, Any]] = []
        for path in sorted(inbox.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(".") and not self._allow_hidden_files():
                continue
            self._validate_file(path)
            checksum = _sha256(path)
            files.append(
                {
                    "file_name": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "sha256": checksum,
                    "already_processed": checksum in self._seen_hashes,
                    "external_ref": self._file_ref(path, "file"),
                }
            )
        return files

    def _import_file(self, file_name: str, *, object_type: str) -> dict[str, Any]:
        path = self._safe_child(self._inbox_dir(), file_name)
        self._validate_file(path)
        checksum = _sha256(path)
        if checksum in self._seen_hashes:
            return {
                "status": "skipped",
                "reason": "duplicate_checksum",
                "sha256": checksum,
                "external_ref": self._file_ref(path, object_type),
            }
        try:
            import_result = self._parse_file(path, object_type=object_type)
        except Exception:
            if self._move_after_processing() and self.config.mode != "dry_run":
                self._move_file(path, self._failed_dir())
            raise
        self._seen_hashes.add(checksum)
        if self._move_after_processing() and self.config.mode != "dry_run":
            self._move_file(path, self._processed_dir())
        return {
            "status": "imported",
            "sha256": checksum,
            "file_name": path.name,
            "import_result": import_result,
            "external_ref": self._file_ref(path, object_type),
        }

    def _parse_file(
        self,
        path: Path,
        *,
        object_type: str,
    ) -> AssayImportResult | list[dict[str, Any]]:
        suffix = path.suffix.lower()
        if object_type == "assay_result" and suffix in {".csv", ".json"}:
            return import_assay_results(
                path,
                input_format="auto",
                source_type="connected_system",
            )
        if suffix == ".csv":
            with path.open(newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        if suffix == ".json":
            raw = json.loads(path.read_text())
            if isinstance(raw, list):
                return [row for row in raw if isinstance(row, dict)]
            if isinstance(raw, dict):
                rows = raw.get("records") or raw.get("items") or raw.get("results") or []
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            raise ConnectorError("JSON file must contain a list or records/items/results array.")
        if suffix == ".jsonl":
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if suffix == ".parquet":
            return self._parse_parquet(path)
        raise ConnectorError(f"Unsupported file type: {suffix}")

    def _parse_parquet(self, path: Path) -> list[dict[str, Any]]:
        try:
            pd = import_module("pandas")
        except ImportError as exc:
            raise ConnectorError(
                "Parquet import requires optional pandas/pyarrow dependencies."
            ) from exc
        return pd.read_parquet(path).to_dict(orient="records")

    def _write_export_file(
        self,
        records: list[dict[str, Any]],
        *,
        file_name: str,
        file_format: str,
    ) -> dict[str, Any]:
        outbox = self._outbox_dir()
        outbox.mkdir(parents=True, exist_ok=True)
        path = self._safe_child(outbox, file_name)
        if file_format == "json":
            path.write_text(json.dumps(records, indent=2, sort_keys=True, default=str))
        elif file_format == "csv":
            fieldnames = sorted({key for record in records for key in record})
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
        else:
            raise ConnectorError("Export format must be json or csv.")
        checksum = _sha256(path)
        return {
            "status": "exported",
            "file_name": path.name,
            "sha256": checksum,
            "external_ref": self._file_ref(path, "export_file"),
        }

    def _generate_manifest(self) -> dict[str, Any]:
        scanned = self._scan_inbox()
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "connector_id": self.config.connector_id,
            "root_dir": str(self._root_dir()),
            "files": [
                {
                    "file_name": item["file_name"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                    "already_processed": item["already_processed"],
                }
                for item in scanned
            ],
            "external_ref": ExternalRecordRef(
                external_system_id=self.config.connector_id,
                external_record_type="manifest",
                external_record_id=f"manifest-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                retrieved_at=datetime.now(UTC),
                metadata={},
            ),
        }

    def _validate_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not self._is_inside_root(resolved):
            raise ConnectorError("Path traversal outside configured root is blocked.")
        if not resolved.exists():
            raise ConnectorError(f"File not found: {path.name}")
        if resolved.name.startswith(".") and not self._allow_hidden_files():
            raise ConnectorError("Hidden files are not processed by default.")
        if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ConnectorError(f"Unsupported file type: {resolved.suffix}")
        max_size = int(self.config.config.get("max_file_size_bytes") or DEFAULT_MAX_FILE_SIZE_BYTES)
        if resolved.stat().st_size > max_size:
            raise ConnectorError("File exceeds configured max_file_size_bytes.")

    def _safe_child(self, parent: Path, file_name: str) -> Path:
        if Path(file_name).is_absolute() or ".." in Path(file_name).parts:
            raise ConnectorError("Path traversal outside configured root is blocked.")
        resolved = (parent / file_name).resolve()
        if not self._is_inside_root(resolved):
            raise ConnectorError("Path traversal outside configured root is blocked.")
        return resolved

    def _move_file(self, path: Path, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._safe_child(target_dir, path.name)
        shutil.move(str(path), str(target))
        return target

    def _file_ref(self, path: Path, record_type: str) -> ExternalRecordRef:
        return ExternalRecordRef(
            external_system_id=self.config.connector_id,
            external_record_type=record_type,
            external_record_id=_sha256(path) if path.exists() else path.name,
            retrieved_at=datetime.now(UTC),
            metadata={"file_name": path.name},
        )

    def _root_dir(self) -> Path:
        raw = self.config.config.get("root_dir")
        if not raw:
            raise ConnectorError("generic file connector requires config.root_dir.")
        return Path(str(raw)).expanduser().resolve()

    def _inbox_dir(self) -> Path:
        return self._safe_directory("inbox_dir", "inbox")

    def _processed_dir(self) -> Path:
        return self._safe_directory("processed_dir", "processed")

    def _failed_dir(self) -> Path:
        return self._safe_directory("failed_dir", "failed")

    def _outbox_dir(self) -> Path:
        return self._safe_directory("outbox_dir", "outbox")

    def _safe_directory(self, key: str, default: str) -> Path:
        raw = self.config.config.get(key) or default
        path = Path(str(raw))
        if path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (self._root_dir() / path).resolve()
        if not self._is_inside_root(resolved):
            raise ConnectorError("Path traversal outside configured root is blocked.")
        return resolved

    def _is_inside_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._root_dir())
        except ValueError:
            return False
        return True

    def _allow_hidden_files(self) -> bool:
        return bool(self.config.config.get("allow_hidden_files", False))

    def _move_after_processing(self) -> bool:
        return bool(self.config.config.get("move_after_processing", False))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assay_result_record(result: AssayResult, connector_id: str) -> dict[str, Any]:
    source_id = (
        result.provenance.get("source_record_id")
        or f"{Path(str(result.source_path or 'assay-result')).name}:{result.source_row or 0}"
    )
    return {
        "external_ref": ExternalRecordRef(
            external_system_id=connector_id,
            external_record_type="assay_result",
            external_record_id=str(source_id),
            retrieved_at=result.imported_at,
            metadata={"result_id": result.result_id},
        ),
        "assay_result": result,
    }


GenericCsvSftpConnector = GenericFileConnector
