from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.codex_backbone.schemas import (
    CodexBackboneConfig,
    CodexTask,
    CodexTaskResult,
)

PROHIBITED_OUTPUT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(cures?|treats?|prevents?)\s+[A-Za-z0-9_-]+", "therapeutic claim"),
    (r"\b(is|are|was|were)\s+(active|safe|synthesizable)\b", "unsupported property claim"),
    (r"\bbinds?\s+(to\s+)?[A-Za-z0-9_-]+\b", "binding claim"),
    (r"\bsynthesis routes?\b", "synthesis route"),
    (r"\blab protocols?\b", "lab protocol"),
    (r"\b(animal|human|patient)\s+dos(e|ing)\b", "dosing instruction"),
    (r"\bpatient treatment instructions?\b", "patient treatment instruction"),
)

GROUNDING_LIMITATIONS = [
    "Codex may summarize source-backed artifacts but is not a source of biomedical truth.",
    "Do not invent targets, molecules, assay results, citations, evidence, or scores.",
    "Do not directly alter scores; scoring changes must come from molecule-ranker scoring modules.",
    "Do not claim cure, treatment, binding, activity, safety, or synthesizability.",
    "Do not provide synthesis routes, laboratory protocols, dosing, or patient instructions.",
]


class GuardrailViolation(BaseModel):
    rule: str
    message: str
    text_excerpt: str


class CodexArtifact(BaseModel):
    artifact_id: str
    path: str
    artifact_type: str
    sha256: str
    size_bytes: int

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        artifact_id: str | None = None,
        artifact_type: str | None = None,
    ) -> CodexArtifact:
        resolved = path.resolve()
        data = resolved.read_bytes()
        return cls(
            artifact_id=artifact_id or resolved.stem,
            path=str(resolved),
            artifact_type=artifact_type or _artifact_type(resolved),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )


class CodexProviderConfig(BaseModel):
    mode: Literal["enabled", "dry_run", "disabled"] = "enabled"
    command: list[str] = Field(default_factory=lambda: ["codex", "exec", "--json"])
    timeout_seconds: float = Field(default=120.0, gt=0.0)
    working_dir: str | None = None
    audit_log_path: str = ".codex/molecule-ranker/codex-cli-audit.jsonl"
    require_json_output: bool = True


class CodexRequest(BaseModel):
    task: str
    prompt_sections: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[CodexArtifact] = Field(default_factory=list)
    expected_json_schema: dict[str, Any] | None = None
    output_format: Literal["json", "markdown", "text"] = "json"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexResponse(BaseModel):
    request_id: str
    status: Literal["ok", "dry_run", "disabled", "error", "guardrail_violation"]
    command: list[str] = Field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    parsed_json: dict[str, Any] | None = None
    guardrail_violations: list[GuardrailViolation] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    audit_log_path: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class LLMProvider(Protocol):
    """Common interface for runtime LLM providers."""

    def invoke(self, request: CodexRequest) -> CodexResponse: ...

    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class LLMProviderFactoryConfig(BaseModel):
    enable_codex_backbone: bool = False
    llm_provider: Literal["auto", "codex", "null", "api"] = "auto"
    test_mode: bool = False
    use_null_llm_provider: bool = False
    api_provider_configured: bool = False
    codex_cli_command: str = "codex"
    codex_model: str | None = None
    codex_reasoning_effort: str | None = "high"
    codex_working_dir: Path | None = None
    codex_timeout_seconds: int = Field(default=300, gt=0)
    codex_require_json: bool = True
    codex_dry_run: bool = False
    codex_store_transcripts: bool = True
    codex_guardrails_enabled: bool = True


class CodexCLIProvider:
    """Controlled subprocess provider for Codex CLI orchestration.

    The provider is deliberately artifact-grounded. It can ask Codex to inspect and
    summarize existing molecule-ranker outputs, but guardrails reject unsupported
    biomedical claims and malformed JSON responses.
    """

    def __init__(self, config: CodexProviderConfig | None = None) -> None:
        self.config = config or CodexProviderConfig()

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        mode = self.config.mode
        command = self.config.command[0] if self.config.command else "codex"
        backbone_config = CodexBackboneConfig(
            enable_codex_backbone=mode != "disabled",
            codex_cli_command=command,
            codex_working_dir=Path(self.config.working_dir) if self.config.working_dir else None,
            codex_timeout_seconds=int(self.config.timeout_seconds),
            codex_require_json=self.config.require_json_output,
            codex_dry_run=mode == "dry_run",
            codex_store_transcripts=True,
        )
        return CodexBackboneProvider(backbone_config).run_task(task)

    def invoke(self, request: CodexRequest) -> CodexResponse:
        request_id = str(uuid4())
        started = datetime.now(UTC)
        prompt = self.build_prompt(request)
        command = list(self.config.command)
        cwd = Path(self.config.working_dir or ".").resolve()
        response = CodexResponse(
            request_id=request_id,
            status="error",
            command=command,
            started_at=started,
            audit_log_path=self.config.audit_log_path,
        )

        if self.config.mode == "disabled":
            response.status = "disabled"
            response.stderr = "Codex CLI provider is disabled."
            response.completed_at = datetime.now(UTC)
            self._write_audit(request, response)
            return response

        input_violations = check_guardrails(request.task)
        if input_violations:
            response.status = "guardrail_violation"
            response.guardrail_violations = input_violations
            response.stderr = "Prompt failed Codex guardrail validation."
            response.completed_at = datetime.now(UTC)
            self._write_audit(request, response)
            return response

        if self.config.mode == "dry_run":
            response.status = "dry_run"
            response.stdout = self._dry_run_payload(request, prompt)
            response.parsed_json = json.loads(response.stdout)
            response.completed_at = datetime.now(UTC)
            self._write_audit(request, response)
            return response

        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                cwd=cwd,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            response.stderr = f"Codex CLI command not found: {command[0]}"
            response.completed_at = datetime.now(UTC)
            self._write_audit(request, response)
            raise RuntimeError(response.stderr) from exc
        except subprocess.TimeoutExpired as exc:
            response.stderr = f"Codex CLI timed out after {self.config.timeout_seconds} seconds."
            response.stdout = _text_output(exc.stdout)
            response.completed_at = datetime.now(UTC)
            self._write_audit(request, response)
            return response

        response.returncode = completed.returncode
        response.stdout = completed.stdout
        response.stderr = completed.stderr
        response.completed_at = datetime.now(UTC)
        output_violations = check_guardrails(completed.stdout)
        if output_violations:
            response.status = "guardrail_violation"
            response.guardrail_violations = output_violations
            self._write_audit(request, response)
            return response
        if completed.returncode != 0:
            response.status = "error"
            self._write_audit(request, response)
            return response
        if self.config.require_json_output or request.output_format == "json":
            try:
                response.parsed_json = _extract_json_object(completed.stdout)
                _validate_expected_json(response.parsed_json, request.expected_json_schema)
            except ValueError as exc:
                response.status = "error"
                response.stderr = f"{response.stderr}\n{exc}".strip()
                self._write_audit(request, response)
                return response
        response.status = "ok"
        self._write_audit(request, response)
        return response

    def build_prompt(self, request: CodexRequest) -> str:
        payload = {
            "task": request.task,
            "role": "molecule-ranker Codex CLI orchestrator",
            "grounding_limitations": GROUNDING_LIMITATIONS,
            "artifact_manifest": [
                artifact.model_dump(mode="json") for artifact in request.artifacts
            ],
            "prompt_sections": request.prompt_sections,
            "expected_json_schema": request.expected_json_schema,
            "output_format": request.output_format,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _dry_run_payload(self, request: CodexRequest, prompt: str) -> str:
        payload = {
            "status": "dry_run",
            "task": request.task,
            "artifact_count": len(request.artifacts),
            "artifact_ids": [artifact.artifact_id for artifact in request.artifacts],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "limitations": GROUNDING_LIMITATIONS,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _write_audit(self, request: CodexRequest, response: CodexResponse) -> None:
        audit_path = Path(self.config.audit_log_path)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        with audit_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


class NullLLMProvider:
    """Deterministic no-op provider for tests and explicit null mode."""

    def invoke(self, request: CodexRequest) -> CodexResponse:
        now = datetime.now(UTC)
        parsed = {
            "provider": "null",
            "task": request.task,
            "artifact_count": len(request.artifacts),
            "artifact_ids": [artifact.artifact_id for artifact in request.artifacts],
        }
        return CodexResponse(
            request_id=str(uuid4()),
            status="ok",
            stdout=json.dumps(parsed, indent=2, sort_keys=True),
            parsed_json=parsed,
            started_at=now,
            completed_at=now,
        )

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        now = datetime.now(UTC)
        output_json = {
            "provider": "null",
            "task_id": task.task_id,
            "task_type": task.task_type,
            "artifact_refs": list(task.input_artifact_paths),
            "message": "NullLLMProvider returned deterministic test output.",
        }
        output_text = json.dumps(output_json, indent=2, sort_keys=True)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=output_text,
            output_json=output_json,
            stdout=output_text,
            stderr="",
            return_code=0,
            artifacts_read=list(task.input_artifact_paths),
            artifacts_written=[],
            commands_observed=[],
            guardrail_warnings=[],
            usage_summary={"provider": "null"},
            started_at=now,
            completed_at=now,
            metadata={"provider": "null"},
        )


class APILLMProvider:
    """Explicit placeholder for optional API-backed providers.

    This class intentionally does not infer credentials from the environment. A real
    API implementation must be configured deliberately by the caller.
    """

    def __init__(self, provider_name: str = "api") -> None:
        self.provider_name = provider_name

    def invoke(self, request: CodexRequest) -> CodexResponse:
        raise RuntimeError(
            "API LLM provider was explicitly selected, but no API provider "
            "implementation is configured."
        )

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        now = datetime.now(UTC)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="failed",
            stderr=(
                "API LLM provider was explicitly selected, but no API provider "
                "implementation is configured."
            ),
            return_code=None,
            started_at=now,
            completed_at=now,
            metadata={"provider": self.provider_name},
        )


def create_llm_provider(config: Any | None = None) -> LLMProvider:
    """Choose the runtime LLM provider without assuming API credentials exist."""

    provider_config = _provider_factory_config(config)
    if provider_config.enable_codex_backbone or provider_config.llm_provider == "codex":
        return CodexCLIProvider(_codex_provider_config(provider_config))
    if (
        provider_config.test_mode
        or provider_config.use_null_llm_provider
        or provider_config.llm_provider == "null"
    ):
        return NullLLMProvider()
    if provider_config.llm_provider == "api" or provider_config.api_provider_configured:
        return APILLMProvider()
    return NullLLMProvider()


def _provider_factory_config(config: Any | None) -> LLMProviderFactoryConfig:
    if config is None:
        return LLMProviderFactoryConfig()
    if isinstance(config, LLMProviderFactoryConfig):
        return config
    if isinstance(config, CodexBackboneConfig):
        return LLMProviderFactoryConfig(**config.model_dump())
    if isinstance(config, BaseModel):
        return LLMProviderFactoryConfig(**_known_provider_fields(config.model_dump()))
    if isinstance(config, dict):
        return LLMProviderFactoryConfig(**_known_provider_fields(config))
    raise TypeError(f"Unsupported LLM provider config type: {type(config).__name__}")


def _known_provider_fields(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(LLMProviderFactoryConfig.model_fields)
    normalized = dict(payload)
    if "null_llm" in normalized and "use_null_llm_provider" not in normalized:
        normalized["use_null_llm_provider"] = normalized["null_llm"]
    if "use_null_llm" in normalized and "use_null_llm_provider" not in normalized:
        normalized["use_null_llm_provider"] = normalized["use_null_llm"]
    return {key: value for key, value in normalized.items() if key in allowed}


def _codex_provider_config(config: LLMProviderFactoryConfig) -> CodexProviderConfig:
    mode: Literal["enabled", "dry_run", "disabled"] = (
        "dry_run" if config.codex_dry_run else "enabled"
    )
    return CodexProviderConfig(
        mode=mode,
        command=shlex.split(config.codex_cli_command) or ["codex"],
        timeout_seconds=float(config.codex_timeout_seconds),
        working_dir=str(config.codex_working_dir) if config.codex_working_dir else None,
        require_json_output=config.codex_require_json,
    )


def check_guardrails(text: str) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    for pattern, label in PROHIBITED_OUTPUT_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            excerpt = text[max(0, match.start() - 40) : match.end() + 40]
            violations.append(
                GuardrailViolation(
                    rule=label,
                    message=f"Codex output contains a prohibited {label}.",
                    text_excerpt=excerpt,
                )
            )
    return violations


def _artifact_type(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "json"
    if path.suffix.lower() in {".md", ".markdown"}:
        return "markdown"
    if path.suffix.lower() in {".html", ".htm"}:
        return "html"
    return "artifact"


def _extract_json_object(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        raise ValueError("Codex CLI returned empty stdout; expected JSON.")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Codex CLI output did not contain a JSON object.") from None
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Codex CLI JSON output must be an object.")
    return parsed


def _validate_expected_json(
    payload: dict[str, Any] | None,
    schema: dict[str, Any] | None,
) -> None:
    if schema is None or payload is None:
        return
    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"Codex JSON output missing required fields: {', '.join(missing)}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    type_map = {
        "array": list,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "object": dict,
        "string": str,
    }
    for key, spec in properties.items():
        if key not in payload or not isinstance(spec, dict):
            continue
        raw_expected = spec.get("type")
        expected = raw_expected if isinstance(raw_expected, str) else None
        if expected is None:
            continue
        expected_type = type_map.get(expected)
        if expected_type is not None and not isinstance(payload[key], expected_type):
            raise ValueError(f"Codex JSON field {key!r} must be {expected}.")


def _text_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
