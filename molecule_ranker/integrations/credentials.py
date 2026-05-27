from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, field_validator, model_validator

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.schemas import (
    CredentialType,
    IntegrationAuditEvent,
    IntegrationCredential,
    IntegrationCredentialCreate,
    IntegrationCredentialRef,
)

SecretRefType = Literal["env", "local_encrypted_file", "external_secret_manager"]

SECRET_REF_PATTERN = re.compile(
    r"^(?P<kind>env|local_encrypted_file|external_secret_manager):(?P<reference>.+)$"
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s\"']{4,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


class CredentialError(ValueError):
    """Raised when credential references are invalid or cannot be resolved."""


class SecretRef(BaseModel):
    ref_type: SecretRefType
    reference: str

    @field_validator("reference")
    @classmethod
    def require_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("secret reference must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_reference_shape(self) -> SecretRef:
        if self.ref_type == "env" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.reference):
            raise ValueError("environment secret references must be environment variable names")
        return self

    @classmethod
    def parse(cls, raw: str) -> SecretRef:
        match = SECRET_REF_PATTERN.match(raw.strip())
        if not match:
            raise CredentialError(
                "secret_ref must use env:NAME, local_encrypted_file:/path, "
                "or external_secret_manager:provider/path"
            )
        return cls(
            ref_type=cast(SecretRefType, match.group("kind")),
            reference=match.group("reference"),
        )

    def as_string(self) -> str:
        return f"{self.ref_type}:{self.reference}"


class CredentialResolver:
    def __init__(self, *, registry_path: Path | None = None, root_dir: Path | None = None) -> None:
        self.registry_path = _registry_path(registry_path=registry_path, root_dir=root_dir)

    def resolve_credential(self, credential_id: str) -> str:
        credential = _get_credential(self.registry_path, credential_id)
        if credential is None:
            raise CredentialError(f"Credential not found: {credential_id}")
        secret_ref = SecretRef.parse(credential["secret_ref"])
        return _resolve_secret_ref(secret_ref)

    def validate_credential(self, credential_id: str) -> dict[str, Any]:
        credential = _get_credential(self.registry_path, credential_id)
        if credential is None:
            raise CredentialError(f"Credential not found: {credential_id}")
        secret_ref = validate_secret_ref(str(credential["secret_ref"]))
        try:
            resolved = _resolve_secret_ref(secret_ref)
        except CredentialError as exc:
            return {
                "credential_id": credential_id,
                "ok": False,
                "ref_type": secret_ref.ref_type,
                "message": redact_secret_values(str(exc)),
            }
        return {
            "credential_id": credential_id,
            "ok": bool(resolved),
            "ref_type": secret_ref.ref_type,
            "message": "Credential reference resolved.",
        }


def resolve_credential(
    credential_id: str,
    *,
    registry_path: Path | None = None,
    root_dir: Path | None = None,
) -> str:
    return CredentialResolver(registry_path=registry_path, root_dir=root_dir).resolve_credential(
        credential_id
    )


def validate_secret_ref(secret_ref: str) -> SecretRef:
    return SecretRef.parse(secret_ref)


def redact_secret_values(text: str) -> str:
    redacted = redact_secrets(text)
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    for name, value in os.environ.items():
        lowered = name.lower()
        if not value or len(value) < 4:
            continue
        if any(marker in lowered for marker in ["key", "token", "secret", "password"]):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def list_credentials_redacted(
    *,
    registry_path: Path | None = None,
    root_dir: Path | None = None,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    registry = _load_registry(_registry_path(registry_path=registry_path, root_dir=root_dir))
    credentials = []
    for credential in registry["credentials"]:
        metadata = dict(credential.get("metadata") or {})
        if metadata.get("deleted_at") and not include_deleted:
            continue
        credentials.append(_redacted_credential(credential))
    return credentials


def create_credential_reference(
    *,
    external_system_id: str,
    credential_type: CredentialType,
    secret_ref: str,
    registry_path: Path | None = None,
    root_dir: Path | None = None,
    credential_id: str | None = None,
    expires_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> IntegrationCredential:
    parsed_ref = validate_secret_ref(secret_ref)
    now = datetime.now(UTC)
    credential = IntegrationCredential(
        credential_id=credential_id or f"cred-{uuid4().hex[:16]}",
        external_system_id=external_system_id,
        credential_type=credential_type,
        secret_ref=parsed_ref.as_string(),
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        last_used_at=None,
        metadata={
            "status": "active",
                "rotation": {
                    "created_at": now.isoformat(),
                    "rotated_at": None,
                    "previous_reference": None,
                },
            **_redact_json(metadata or {}),
        },
    )
    path = _registry_path(registry_path=registry_path, root_dir=root_dir)
    registry = _load_registry(path)
    registry["credentials"] = [
        item
        for item in registry["credentials"]
        if item["credential_id"] != credential.credential_id
    ]
    registry["credentials"].append(credential.model_dump(mode="json"))
    _append_audit(
        registry,
        external_system_id=external_system_id,
        actor_user_id=actor_user_id,
        event_type="integration_credential_created",
        object_id=credential.credential_id,
        summary=f"Created integration credential reference {credential.credential_id}.",
        metadata={"credential_id": credential.credential_id, "secret_ref": parsed_ref.as_string()},
    )
    _save_registry(path, registry)
    return credential


def delete_credential_reference(
    credential_id: str,
    *,
    registry_path: Path | None = None,
    root_dir: Path | None = None,
    actor_user_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    path = _registry_path(registry_path=registry_path, root_dir=root_dir)
    registry = _load_registry(path)
    now = datetime.now(UTC)
    for credential in registry["credentials"]:
        if credential["credential_id"] != credential_id:
            continue
        metadata = dict(credential.get("metadata") or {})
        metadata["status"] = "revoked"
        metadata["revoked_at"] = now.isoformat()
        metadata["deleted_at"] = now.isoformat()
        if reason:
            metadata["deletion_reason"] = redact_secret_values(reason)
        credential["metadata"] = _redact_json(metadata)
        credential["updated_at"] = now.isoformat()
        _append_audit(
            registry,
            external_system_id=str(credential["external_system_id"]),
            actor_user_id=actor_user_id,
            event_type="integration_credential_revoked",
            object_id=credential_id,
            summary=f"Revoked integration credential reference {credential_id}.",
            metadata={"credential_id": credential_id, "reason": reason or ""},
        )
        _save_registry(path, registry)
        return _redacted_credential(credential)
    raise CredentialError(f"Credential not found: {credential_id}")


def _resolve_secret_ref(secret_ref: SecretRef) -> str:
    if secret_ref.ref_type == "env":
        value = os.getenv(secret_ref.reference)
        if not value:
            raise CredentialError(
                f"Environment variable {secret_ref.reference} is not set for credential reference."
            )
        return value
    if secret_ref.ref_type == "local_encrypted_file":
        path = Path(secret_ref.reference).expanduser()
        if not path.exists():
            raise CredentialError("Local encrypted credential file does not exist.")
        raise CredentialError("Local encrypted credential file resolution is not configured.")
    raise CredentialError("External secret manager resolution is not configured.")


def _registry_path(*, registry_path: Path | None, root_dir: Path | None) -> Path:
    if registry_path is not None:
        return registry_path
    root = (root_dir or Path(".")).resolve()
    return root / ".molecule-ranker" / "integration_credentials.json"


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"credentials": [], "audit_events": []}
    payload = json.loads(path.read_text())
    return {
        "credentials": list(payload.get("credentials") or []),
        "audit_events": list(payload.get("audit_events") or []),
    }


def _save_registry(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_redact_json(registry), indent=2, sort_keys=True) + "\n")


def _get_credential(path: Path, credential_id: str) -> dict[str, Any] | None:
    registry = _load_registry(path)
    for credential in registry["credentials"]:
        if credential.get("credential_id") == credential_id:
            metadata = dict(credential.get("metadata") or {})
            if metadata.get("deleted_at"):
                raise CredentialError(f"Credential is revoked: {credential_id}")
            return credential
    return None


def _redacted_credential(credential: dict[str, Any]) -> dict[str, Any]:
    payload = dict(credential)
    payload["secret_ref"] = _redact_secret_ref(str(payload.get("secret_ref") or ""))
    payload.pop("secret_value", None)
    payload["metadata"] = _redact_json(dict(payload.get("metadata") or {}))
    return payload


def _redact_secret_ref(secret_ref: str) -> str:
    try:
        parsed = SecretRef.parse(secret_ref)
    except CredentialError:
        return "[REDACTED_SECRET_REF]"
    if parsed.ref_type == "env":
        return parsed.as_string()
    return f"{parsed.ref_type}:[REDACTED_REF]"


def _append_audit(
    registry: dict[str, Any],
    *,
    external_system_id: str,
    actor_user_id: str | None,
    event_type: str,
    object_id: str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    registry["audit_events"].append(
        IntegrationAuditEvent(
            event_id=f"evt-{uuid4().hex[:16]}",
            external_system_id=external_system_id,
            sync_job_id=None,
            actor_user_id=actor_user_id,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            object_type="integration_credential",
            object_id=object_id,
            summary=redact_secret_values(summary),
            metadata=_redact_json(metadata),
        ).model_dump(mode="json")
    )


def _redact_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(redact_secret_values(json.dumps(value, sort_keys=True, default=str)))


__all__ = [
    "CredentialError",
    "CredentialResolver",
    "IntegrationCredential",
    "IntegrationCredentialCreate",
    "IntegrationCredentialRef",
    "SecretRef",
    "create_credential_reference",
    "delete_credential_reference",
    "list_credentials_redacted",
    "redact_secret_values",
    "resolve_credential",
    "validate_secret_ref",
]
