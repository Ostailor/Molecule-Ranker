from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.database import (
    artifact_records,
    platform_audit_events,
    service_account_tokens,
    users,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount

SecurityAuditStatus = Literal["pass", "fail"]

SECURITY_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("credential_storage", "password_hashes_not_plaintext", "Password hashes are not plaintext."),
    ("credential_storage", "service_tokens_not_recoverable", "Service tokens are not recoverable."),
    ("secret_handling", "api_keys_secrets_redacted", "API keys and secrets are redacted."),
    ("artifact_access", "env_files_never_served", ".env files are never served."),
    ("artifact_access", "cache_files_not_downloadable", "Cache files are not downloadable."),
    ("artifact_access", "artifact_path_traversal_blocked", "Artifact path traversal is blocked."),
    ("auth_rbac", "hosted_routes_require_auth", "Hosted routes require authentication."),
    ("auth_rbac", "rbac_tests_pass", "RBAC checks pass."),
    ("codex_worker", "codex_jobs_require_codex_permission", "Codex jobs require codex permission."),
    ("codex_worker", "codex_worker_scoped_files_only", "Codex worker cannot read arbitrary files."),
    ("integrations", "webhook_signatures_required", "Webhook signatures are required."),
    ("database", "sql_injection_protections_active", "SQL injection protections are active."),
    (
        "integrations",
        "file_connector_path_traversal_blocked",
        "File connector path traversal is blocked.",
    ),
    (
        "warehouse",
        "warehouse_queries_parameterized_or_allowlisted",
        "Warehouse queries are parameterized or allowlisted.",
    ),
    ("exports", "export_packages_exclude_secrets", "Export packages exclude secrets."),
    ("audit_logs", "audit_logs_do_not_include_secrets", "Audit logs do not include secrets."),
    ("configuration", "config_show_redacts_secrets", "config show redacts secrets."),
)

SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|credential)", re.I)
CACHE_MARKERS = (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
SQL_LITERAL_FILTER_RE = re.compile(r"(=|<|>|<=|>=|<>|!=)\s*('[^']*'|\d+(?:\.\d+)?)")
SQL_UNSAFE_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|merge|copy|grant|revoke|call|execute)\b",
    re.I,
)


@dataclass(frozen=True)
class SecurityAuditFinding:
    check_id: str
    category: str
    severity: str
    message: str
    location: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {
            "check_id": self.check_id,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SecurityAuditCheck:
    check_id: str
    category: str
    description: str
    status: SecurityAuditStatus

    def as_dict(self) -> dict[str, str]:
        return {
            "check_id": self.check_id,
            "category": self.category,
            "description": self.description,
            "status": self.status,
        }


@dataclass(frozen=True)
class SecurityAuditReport:
    status: SecurityAuditStatus
    root_dir: Path
    checks: list[SecurityAuditCheck]
    findings: list[SecurityAuditFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root_dir": str(self.root_dir),
            "check_count": len(self.checks),
            "finding_count": len(self.findings),
            "checks": [check.as_dict() for check in self.checks],
            "findings": [finding.as_dict() for finding in self.findings],
        }


def run_security_audit(
    *,
    root_dir: str | Path = ".",
    database_url: str | None = None,
    db_path: str | Path | None = None,
) -> SecurityAuditReport:
    root = Path(root_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    findings: list[SecurityAuditFinding] = []
    fixtures = _load_fixture_payloads(root)
    findings.extend(_audit_fixture_payloads(fixtures))
    findings.extend(_audit_local_files(root))
    if database_url is not None or db_path is not None:
        database = PlatformDatabase(
            root,
            database_url=database_url,
            db_path=Path(db_path) if db_path else None,
        )
        findings.extend(_audit_database(database))
    findings.extend(_audit_active_rbac_self_check())

    checks = [
        SecurityAuditCheck(
            check_id=check_id,
            category=category,
            description=description,
            status="fail" if any(finding.check_id == check_id for finding in findings) else "pass",
        )
        for category, check_id, description in SECURITY_CHECKS
    ]
    report = SecurityAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        checks=checks,
        findings=findings,
    )
    write_security_audit_reports(report)
    return report


def write_security_audit_reports(report: SecurityAuditReport) -> None:
    report.root_dir.mkdir(parents=True, exist_ok=True)
    (report.root_dir / "security_audit.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    (report.root_dir / "security_audit.md").write_text(_render_markdown(report))


def _audit_fixture_payloads(
    payloads: Iterable[tuple[Path, dict[str, Any]]],
) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for path, payload in payloads:
        location = path.name
        findings.extend(_audit_fixture_credentials(location, payload))
        findings.extend(_audit_fixture_artifacts(location, payload))
        findings.extend(_audit_fixture_routes(location, payload))
        findings.extend(_audit_fixture_codex(location, payload))
        findings.extend(_audit_fixture_integrations(location, payload))
        findings.extend(_audit_fixture_sql(location, payload))
        findings.extend(_audit_fixture_exports_and_logs(location, payload))
    return findings


def _audit_fixture_credentials(
    location: str,
    payload: dict[str, Any],
) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, user in enumerate(_list(payload.get("users"))):
        password_hash = str(user.get("password_hash") or "")
        if _looks_plaintext_password(password_hash):
            findings.append(
                _finding(
                    "password_hashes_not_plaintext",
                    "credential_storage",
                    location,
                    "Password hash fixture looks plaintext.",
                    f"users[{index}].password_hash",
                )
            )
    for index, token in enumerate(_list(payload.get("service_tokens"))):
        token_hash = str(token.get("token_hash") or "")
        raw_token = str(token.get("token") or token.get("access_token") or "")
        if raw_token and (token_hash == raw_token or token_hash.startswith("mrs_")):
            findings.append(
                _finding(
                    "service_tokens_not_recoverable",
                    "credential_storage",
                    location,
                    "Service token fixture stores recoverable token material.",
                    f"service_tokens[{index}].token_hash",
                )
            )
    return findings


def _audit_fixture_artifacts(location: str, payload: dict[str, Any]) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, artifact in enumerate(_list(payload.get("artifacts"))):
        artifact_path = str(artifact.get("path") or "")
        marker = f"artifacts[{index}].path={artifact_path}"
        if artifact_path.endswith(".env") or Path(artifact_path).name == ".env":
            if artifact.get("served") is True or artifact.get("downloadable") is True:
                findings.append(
                    _finding(
                        "env_files_never_served",
                        "artifact_access",
                        location,
                        ".env artifact is marked served or downloadable.",
                        marker,
                    )
                )
        cache_downloadable = (
            any(part in artifact_path for part in CACHE_MARKERS)
            and artifact.get("downloadable") is True
        )
        if cache_downloadable:
            findings.append(
                _finding(
                    "cache_files_not_downloadable",
                    "artifact_access",
                    location,
                    "Cache artifact is marked downloadable.",
                    marker,
                )
            )
        if _has_path_traversal(artifact_path) and artifact.get("downloadable") is True:
            findings.append(
                _finding(
                    "artifact_path_traversal_blocked",
                    "artifact_access",
                    location,
                    "Artifact path traversal is marked downloadable.",
                    marker,
                )
            )
    return findings


def _audit_fixture_routes(location: str, payload: dict[str, Any]) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, route in enumerate(_list(payload.get("routes"))):
        if route.get("hosted") is True and route.get("auth_required") is False:
            findings.append(
                _finding(
                    "hosted_routes_require_auth",
                    "auth_rbac",
                    location,
                    "Hosted route is marked unauthenticated.",
                    f"routes[{index}].path={route.get('path')}",
                )
            )
    rbac = payload.get("rbac")
    if isinstance(rbac, dict) and rbac.get("tests_pass") is False:
        findings.append(
            _finding(
                "rbac_tests_pass",
                "auth_rbac",
                location,
                "RBAC fixture reports failing tests.",
                "rbac.tests_pass=false",
            )
        )
    return findings


def _audit_fixture_codex(location: str, payload: dict[str, Any]) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, job in enumerate(_list(payload.get("codex_jobs"))):
        if job.get("permission_required") != "codex:run":
            findings.append(
                _finding(
                    "codex_jobs_require_codex_permission",
                    "codex_worker",
                    location,
                    "Codex job does not require codex:run.",
                    f"codex_jobs[{index}].permission_required",
                )
            )
        for artifact_path in _list(job.get("allowed_artifact_paths")):
            if str(artifact_path).startswith("/") or _has_path_traversal(str(artifact_path)):
                findings.append(
                    _finding(
                        "codex_worker_scoped_files_only",
                        "codex_worker",
                        location,
                        "Codex job allows arbitrary file access outside scoped artifacts.",
                        str(artifact_path),
                    )
                )
    return findings


def _audit_fixture_integrations(
    location: str,
    payload: dict[str, Any],
) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, webhook in enumerate(_list(payload.get("webhooks"))):
        if webhook.get("signature_required") is False:
            findings.append(
                _finding(
                    "webhook_signatures_required",
                    "integrations",
                    location,
                    "Webhook fixture accepts unsigned payloads.",
                    f"webhooks[{index}].path={webhook.get('path')}",
                )
            )
    for index, connector in enumerate(_list(payload.get("file_connectors"))):
        connector_path = str(connector.get("path") or "")
        if _has_path_traversal(connector_path):
            findings.append(
                _finding(
                    "file_connector_path_traversal_blocked",
                    "integrations",
                    location,
                    "File connector fixture includes path traversal.",
                    f"file_connectors[{index}].path={connector_path}",
                )
            )
    return findings


def _audit_fixture_sql(location: str, payload: dict[str, Any]) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for index, query_item in enumerate(_list(payload.get("sql"))):
        query = str(query_item.get("query") or "")
        if _unsafe_sql(query):
            findings.append(
                _finding(
                    "sql_injection_protections_active",
                    "database",
                    location,
                    "SQL fixture contains unsafe or literal-filtered query.",
                    f"sql[{index}]",
                )
            )
    for index, query_item in enumerate(_list(payload.get("warehouse"))):
        query = str(query_item.get("query") or "")
        has_params = bool(query_item.get("parameters")) or ":" in query
        allowlisted = bool(query_item.get("allowlisted"))
        if _unsafe_sql(query) or (not has_params and not allowlisted):
            findings.append(
                _finding(
                    "warehouse_queries_parameterized_or_allowlisted",
                    "warehouse",
                    location,
                    "Warehouse query fixture is not parameterized or allowlisted.",
                    f"warehouse[{index}]",
                )
            )
    return findings


def _audit_fixture_exports_and_logs(
    location: str,
    payload: dict[str, Any],
) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    if _contains_secret(payload.get("logs")):
        findings.append(
            _finding(
                "api_keys_secrets_redacted",
                "secret_handling",
                location,
                "Log fixture contains unredacted API key or secret.",
                "logs",
            )
        )
    for index, artifact in enumerate(_list(payload.get("artifacts"))):
        if artifact.get("contains_secret") is True:
            findings.append(
                _finding(
                    "export_packages_exclude_secrets",
                    "exports",
                    location,
                    "Export fixture includes secret-bearing artifact.",
                    f"artifacts[{index}]",
                )
            )
    if _contains_secret(payload.get("audit_logs")):
        findings.append(
            _finding(
                "audit_logs_do_not_include_secrets",
                "audit_logs",
                location,
                "Audit log fixture contains unredacted secret material.",
                "audit_logs",
            )
        )
    config_show = payload.get("config_show")
    if isinstance(config_show, dict):
        if config_show.get("redacted") is False or _contains_secret(config_show):
            findings.append(
                _finding(
                    "config_show_redacts_secrets",
                    "configuration",
                    location,
                    "config show fixture exposes a secret.",
                    "config_show",
                )
            )
    return findings


def _audit_local_files(root: Path) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in {"security_audit.json", "security_audit.md"}:
            continue
        if path.suffix.lower() not in {".json", ".md", ".txt", ".html", ".log"}:
            continue
        if path.name.startswith("security_bad_fixtures"):
            continue
        relative = path.relative_to(root)
        if _skip_local_secret_scan(relative):
            continue
        text = path.read_text(errors="ignore")
        if _contains_unredacted_secret_text(text):
            check_id = (
                "audit_logs_do_not_include_secrets"
                if "audit" in path.name.lower()
                else "api_keys_secrets_redacted"
            )
            findings.append(
                _finding(
                    check_id,
                    "secret_handling",
                    path.relative_to(root).as_posix(),
                    "Local text artifact contains unredacted secret-looking material.",
                    _redacted_excerpt(text),
                )
            )
    return findings


def _audit_database(database: PlatformDatabase) -> list[SecurityAuditFinding]:
    findings: list[SecurityAuditFinding] = []
    with database.engine.connect() as connection:
        for row in connection.execute(select(users)).mappings():
            password_hash = str(row["password_hash"] or "")
            if _looks_plaintext_password(password_hash):
                findings.append(
                    _finding(
                        "password_hashes_not_plaintext",
                        "credential_storage",
                        "users",
                        "User password hash looks plaintext.",
                        str(row["email"]),
                    )
                )
        for row in connection.execute(select(service_account_tokens)).mappings():
            token_hash = str(row["token_hash"] or "")
            token_salt = str(row["token_salt"] or "")
            if token_hash.startswith("mrs_") or token_hash == token_salt:
                findings.append(
                    _finding(
                        "service_tokens_not_recoverable",
                        "credential_storage",
                        "service_account_tokens",
                        "Service token hash looks recoverable.",
                        str(row["token_id"]),
                    )
                )
        for row in connection.execute(select(artifact_records)).mappings():
            artifact_path = str(row["path"] or "")
            if Path(artifact_path).name == ".env":
                findings.append(
                    _finding(
                        "env_files_never_served",
                        "artifact_access",
                        "artifact_records",
                        ".env file is registered as an artifact.",
                        str(row["artifact_id"]),
                    )
                )
            if any(marker in artifact_path.lower() for marker in CACHE_MARKERS):
                findings.append(
                    _finding(
                        "cache_files_not_downloadable",
                        "artifact_access",
                        "artifact_records",
                        "Cache file is registered as an artifact.",
                        str(row["artifact_id"]),
                    )
                )
        for row in connection.execute(select(platform_audit_events)).mappings():
            payload = json.dumps(
                {
                    "summary": row["summary"],
                    "metadata": row["metadata_json"],
                    "before": row["before_json"],
                    "after": row["after_json"],
                },
                default=str,
                sort_keys=True,
            )
            if redact_secrets(payload) != payload:
                findings.append(
                    _finding(
                        "audit_logs_do_not_include_secrets",
                        "audit_logs",
                        "platform_audit_events",
                        "Audit event contains unredacted secret material.",
                        str(row["event_id"]),
                    )
                )
    return findings


def _audit_active_rbac_self_check() -> list[SecurityAuditFinding]:
    viewer = UserAccount(
        user_id="security-audit-viewer",
        email="viewer@example.test",
        display_name=None,
        is_active=True,
        is_admin=False,
        auth_provider="local_password",
        metadata={"permissions": ["project:read"]},
    )
    if has_permission(viewer, "codex:run", project_id="project-1", database=None):
        return [
            _finding(
                "rbac_tests_pass",
                "auth_rbac",
                "rbac_self_check",
                "Viewer-like user unexpectedly has codex:run.",
                "codex:run",
            )
        ]
    return []


def _load_fixture_payloads(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("security_*fixtures*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append((path, payload))
    return payloads


def _unsafe_sql(query: str) -> bool:
    stripped = query.strip()
    return bool(SQL_UNSAFE_RE.search(stripped) or SQL_LITERAL_FILTER_RE.search(stripped))


def _looks_plaintext_password(value: str) -> bool:
    lowered = value.lower()
    return bool(value and ("password" in lowered or lowered.startswith("plaintext")))


def _has_path_traversal(value: str) -> bool:
    path = Path(value)
    return path.is_absolute() or ".." in path.parts or "../" in value or "..\\" in value


def _contains_secret(value: Any) -> bool:
    raw = json.dumps(value, sort_keys=True, default=str) if not isinstance(value, str) else value
    if _contains_unredacted_secret_text(raw):
        return True
    return False


def _skip_local_secret_scan(relative: Path) -> bool:
    parts = set(relative.parts)
    if parts & {".molecule-ranker", ".demo_state", ".pytest_cache", ".ruff_cache", "__pycache__"}:
        return True
    if relative.parts and relative.parts[0] in {"docs", "examples"}:
        return True
    return relative.name == "README.md"


def _contains_unredacted_secret_text(text: str) -> bool:
    if "[REDACTED]" in text or "safe metadata" in text:
        return False
    redacted = redact_secrets(text)
    if redacted == text:
        return False
    changed = _redaction_deltas(text, redacted)
    return any(not _allowed_secret_placeholder(value) for value in changed)


def _redaction_deltas(original: str, redacted: str) -> list[str]:
    values: list[str] = []
    for pattern in _SECRET_DETECTION_PATTERNS:
        for match in pattern.finditer(original):
            values.append(match.group(0))
    if not values and original != redacted:
        values.append(original)
    return values


_SECRET_DETECTION_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_-]?key|openai[_-]?api[_-]?key|secret|token|password|passwd|"
        r"authorization)\s*[:=]\s*([^\s\"']{6,})"
    ),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|openai[_-]?api[_-]?key|secret|token|password|"
        r"passwd|authorization)[\"']?\s*:\s*[\"'])([^\"']{6,})([\"'])"
    ),
    re.compile(r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=(.+)$"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S,
    ),
)


def _allowed_secret_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "example",
            "placeholder",
            "redacted",
            "change-me",
            "strong-password",
            "admin-password",
            "readiness-password",
            "release-gate-password",
            "$(",
            "${",
            "$molecule_ranker",
        )
    )


def _redacted_excerpt(text: str) -> str:
    return redact_secrets(" ".join(text.split()))[:200]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _finding(
    check_id: str,
    category: str,
    location: str,
    message: str,
    evidence: str,
) -> SecurityAuditFinding:
    return SecurityAuditFinding(
        check_id=check_id,
        category=category,
        severity="error",
        location=location,
        message=message,
        evidence=evidence,
    )


def _render_markdown(report: SecurityAuditReport) -> str:
    lines = [
        "# V1.0 Security Release Audit",
        "",
        f"- Status: `{report.status}`",
        f"- Checks: {len(report.checks)}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Checks",
        "",
    ]
    for check in report.checks:
        lines.append(f"- `{check.status}` `{check.check_id}` - {check.description}")
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("No security release audit findings.")
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.check_id}",
                "",
                f"- Category: {finding.category}",
                f"- Severity: {finding.severity}",
                f"- Location: `{finding.location}`",
                f"- Message: {finding.message}",
                f"- Evidence: {finding.evidence}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "SecurityAuditCheck",
    "SecurityAuditFinding",
    "SecurityAuditReport",
    "run_security_audit",
    "write_security_audit_reports",
]
