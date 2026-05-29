"""Predictive model registry backed by local SQLite and disk artifacts."""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from molecule_ranker.codex_backbone.guardrails import is_secret_path, redact_secrets
from molecule_ranker.experiments.model_plugins import ModelPluginRegistry
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEvaluationReport,
    ModelPrediction,
    ModelTrainingRun,
)

SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "authorization")


class ModelRegistry:
    def __init__(self, *, db_path: str | Path, artifact_dir: str | Path) -> None:
        self.db_path = Path(db_path)
        self.artifact_dir = Path(artifact_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def register_model_card(self, model_card: ModelCard, *, actor: str | None = None) -> Path:
        sanitized = _sanitize_json(model_card.model_dump(mode="json"))
        card = ModelCard(**sanitized)
        path = self.artifact_dir / "model_cards" / f"{card.model_id}.json"
        _write_json(path, card.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into model_cards
                    (model_id, payload_json, artifact_path, active, created_at, updated_at)
                values (
                    ?, ?, ?,
                    coalesce((select active from model_cards where model_id = ?), 1),
                    ?, ?
                )
                """,
                (
                    card.model_id,
                    card.model_dump_json(),
                    str(path),
                    card.model_id,
                    card.created_at.isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        self._audit("model_registered", card.model_id, actor=actor)
        return path

    def get_model_card(self, model_id: str) -> ModelCard:
        with self._connect() as connection:
            row = connection.execute(
                "select payload_json, active from model_cards where model_id = ?",
                (model_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Model card not found: {model_id}")
        payload = json.loads(str(row["payload_json"]))
        payload["metadata"] = {
            **dict(payload.get("metadata") or {}),
            "registry_active": bool(row["active"]),
        }
        return ModelCard(**payload)

    def list_models(
        self,
        *,
        active_only: bool = True,
        endpoint_id: str | None = None,
        plugin_name: str | None = None,
    ) -> list[ModelCard]:
        clauses = []
        params: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        query = "select model_id, payload_json, active from model_cards"
        if clauses:
            query += " where " + " and ".join(clauses)
        query += " order by created_at, model_id"
        cards = []
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if endpoint_id and payload.get("endpoint", {}).get("endpoint_id") != endpoint_id:
                continue
            if plugin_name and payload.get("plugin_name") != plugin_name:
                continue
            payload["metadata"] = {
                **dict(payload.get("metadata") or {}),
                "registry_active": bool(row["active"]),
            }
            cards.append(ModelCard(**payload))
        return cards

    def register_training_run(
        self,
        training_run: ModelTrainingRun,
        *,
        actor: str | None = None,
    ) -> Path:
        payload = _sanitize_json(training_run.model_dump(mode="json"))
        path = self.artifact_dir / "training_runs" / f"{training_run.training_run_id}.json"
        _write_json(path, payload)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into training_runs
                    (training_run_id, model_id, dataset_id, payload_json, artifact_path, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    training_run.training_run_id,
                    training_run.model_id,
                    training_run.dataset_id,
                    json.dumps(payload, sort_keys=True),
                    str(path),
                    datetime.now(UTC).isoformat(),
                ),
            )
        self._audit("training_run_registered", training_run.model_id, actor=actor)
        return path

    def register_evaluation_report(
        self,
        evaluation_report: ModelEvaluationReport,
        *,
        actor: str | None = None,
    ) -> Path:
        payload = _sanitize_json(evaluation_report.model_dump(mode="json"))
        path = self.artifact_dir / "evaluation_reports" / f"{evaluation_report.evaluation_id}.json"
        _write_json(path, payload)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into evaluation_reports
                    (evaluation_id, model_id, dataset_id, payload_json, artifact_path, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_report.evaluation_id,
                    evaluation_report.model_id,
                    evaluation_report.dataset_id,
                    json.dumps(payload, sort_keys=True),
                    str(path),
                    datetime.now(UTC).isoformat(),
                ),
            )
        self._audit("evaluation_report_registered", evaluation_report.model_id, actor=actor)
        return path

    def save_prediction_batch(
        self,
        model_id: str,
        batch_id: str,
        predictions: Sequence[ModelPrediction],
        *,
        metadata: Mapping[str, Any] | None = None,
        actor: str | None = None,
    ) -> Path:
        payload = {
            "artifact_type": "ModelPredictionArtifact",
            "batch_id": batch_id,
            "model_id": model_id,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": _sanitize_json(dict(metadata or {})),
            "predictions": [
                _sanitize_json(prediction.model_dump(mode="json")) for prediction in predictions
            ],
        }
        path = self.artifact_dir / "prediction_batches" / f"{batch_id}.json"
        _write_json(path, payload)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into prediction_batches
                    (batch_id, model_id, payload_json, artifact_path, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    model_id,
                    json.dumps(payload, sort_keys=True),
                    str(path),
                    payload["created_at"],
                ),
            )
        self._audit("prediction_batch_saved", model_id, actor=actor)
        return path

    def load_prediction_batch(self, batch_id: str) -> list[ModelPrediction]:
        with self._connect() as connection:
            row = connection.execute(
                "select payload_json from prediction_batches where batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Prediction batch not found: {batch_id}")
        payload = json.loads(str(row["payload_json"]))
        return [ModelPrediction(**prediction) for prediction in payload.get("predictions", [])]

    def deactivate_model(
        self,
        model_id: str,
        *,
        reason: str,
        actor: str | None = None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "update model_cards set active = 0, updated_at = ? where model_id = ?",
                (datetime.now(UTC).isoformat(), model_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Model card not found: {model_id}")
        self._audit("model_deactivated", model_id, actor=actor, payload={"reason": reason})

    def export_model_package(
        self,
        model_id: str,
        output_path: str | Path,
        *,
        include_raw_assay_files: bool = False,
        raw_assay_files: Sequence[str | Path] | None = None,
    ) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "package_type": "molecule_ranker_model_package",
            "model_id": model_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "include_raw_assay_files": include_raw_assay_files,
            "files": [],
        }
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            self._write_package_json(
                archive,
                f"model_cards/{model_id}.json",
                self.get_model_card(model_id).model_dump(mode="json"),
                manifest,
            )
            for row in self._rows_for_model("training_runs", model_id):
                self._write_package_json(
                    archive,
                    f"training_runs/{row['training_run_id']}.json",
                    json.loads(str(row["payload_json"])),
                    manifest,
                )
            for row in self._rows_for_model("evaluation_reports", model_id):
                self._write_package_json(
                    archive,
                    f"evaluation_reports/{row['evaluation_id']}.json",
                    json.loads(str(row["payload_json"])),
                    manifest,
                )
            for row in self._rows_for_model("prediction_batches", model_id):
                self._write_package_json(
                    archive,
                    f"prediction_batches/{row['batch_id']}.json",
                    json.loads(str(row["payload_json"])),
                    manifest,
                )
            if include_raw_assay_files:
                for raw_path in raw_assay_files or []:
                    self._write_raw_assay_file(archive, Path(raw_path), manifest)
            archive.writestr(
                "manifest.json",
                json.dumps(_sanitize_json(manifest), indent=2, sort_keys=True) + "\n",
            )
        self._audit("model_package_exported", model_id, payload={"package_path": str(output)})
        return output

    def import_model_package(
        self,
        package_path: str | Path,
        *,
        actor: str | None = None,
    ) -> list[str]:
        imported_model_ids: list[str] = []
        with zipfile.ZipFile(package_path) as archive:
            _safe_extract_package(archive, self.artifact_dir / "imported_packages" / uuid4().hex)
            for name in archive.namelist():
                if not name.endswith(".json") or name == "manifest.json":
                    continue
                payload = json.loads(archive.read(name).decode("utf-8"))
                if name.startswith("model_cards/"):
                    card = ModelCard(**payload)
                    self.register_model_card(card, actor=actor)
                    imported_model_ids.append(card.model_id)
                elif name.startswith("training_runs/"):
                    self.register_training_run(ModelTrainingRun(**payload), actor=actor)
                elif name.startswith("evaluation_reports/"):
                    self.register_evaluation_report(ModelEvaluationReport(**payload), actor=actor)
                elif name.startswith("prediction_batches/"):
                    predictions = [
                        ModelPrediction(**item) for item in payload.get("predictions", [])
                    ]
                    self.save_prediction_batch(
                        str(payload["model_id"]),
                        str(payload["batch_id"]),
                        predictions,
                        metadata=payload.get("metadata") or {},
                        actor=actor,
                    )
        for model_id in imported_model_ids:
            self._audit("model_package_imported", model_id, actor=actor)
        return imported_model_ids

    def audit_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select event_type, model_id, actor, payload_json, created_at
                from audit_events
                order by id
                """
            ).fetchall()
        return [
            {
                "event_type": str(row["event_type"]),
                "model_id": str(row["model_id"]),
                "actor": row["actor"],
                "payload": json.loads(str(row["payload_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def _write_package_json(
        self,
        archive: zipfile.ZipFile,
        name: str,
        payload: Mapping[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        sanitized = _sanitize_json(dict(payload))
        archive.writestr(name, json.dumps(sanitized, indent=2, sort_keys=True) + "\n")
        manifest["files"].append({"path": name, "kind": name.split("/", 1)[0]})

    def _write_raw_assay_file(
        self,
        archive: zipfile.ZipFile,
        path: Path,
        manifest: dict[str, Any],
    ) -> None:
        if is_secret_path(path):
            raise ValueError(f"Refusing to export secret-like raw assay path: {path}")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        archive_name = f"raw_assay_files/{path.name}"
        archive.writestr(archive_name, redact_secrets(path.read_text(errors="ignore")))
        manifest["files"].append({"path": archive_name, "kind": "raw_assay_file"})

    def _rows_for_model(self, table: str, model_id: str) -> list[sqlite3.Row]:
        allowed = {"training_runs", "evaluation_reports", "prediction_batches"}
        if table not in allowed:
            raise ValueError(f"Unsupported model artifact table: {table}")
        with self._connect() as connection:
            return connection.execute(
                f"select * from {table} where model_id = ? order by created_at",
                (model_id,),
            ).fetchall()

    def _audit(
        self,
        event_type: str,
        model_id: str,
        *,
        actor: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                insert into audit_events
                    (event_type, model_id, actor, payload_json, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    model_id,
                    actor,
                    json.dumps(_sanitize_json(dict(payload or {})), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists model_cards (
                    model_id text primary key,
                    payload_json text not null,
                    artifact_path text not null,
                    active integer not null default 1,
                    created_at text not null,
                    updated_at text not null
                );
                create table if not exists training_runs (
                    training_run_id text primary key,
                    model_id text not null,
                    dataset_id text not null,
                    payload_json text not null,
                    artifact_path text not null,
                    created_at text not null
                );
                create table if not exists evaluation_reports (
                    evaluation_id text primary key,
                    model_id text not null,
                    dataset_id text not null,
                    payload_json text not null,
                    artifact_path text not null,
                    created_at text not null
                );
                create table if not exists prediction_batches (
                    batch_id text primary key,
                    model_id text not null,
                    payload_json text not null,
                    artifact_path text not null,
                    created_at text not null
                );
                create table if not exists audit_events (
                    id integer primary key autoincrement,
                    event_type text not null,
                    model_id text not null,
                    actor text,
                    payload_json text not null,
                    created_at text not null
                );
                """
            )


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = _sanitize_json(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_json(dict(payload)), indent=2, sort_keys=True) + "\n")


def _safe_extract_package(archive: zipfile.ZipFile, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for member in archive.namelist():
        target = destination / member
        resolved_target = target.resolve()
        if not str(resolved_target).startswith(str(destination.resolve())):
            raise ValueError(f"Unsafe model package path: {member}")
        if member.endswith("/"):
            resolved_target.mkdir(parents=True, exist_ok=True)
            continue
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, resolved_target.open("wb") as output:
            shutil.copyfileobj(source, output)


__all__ = ["ModelPluginRegistry", "ModelRegistry"]
