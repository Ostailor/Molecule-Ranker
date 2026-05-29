from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from molecule_ranker.codex_backbone import (
    CodexBackboneConfig,
    CodexBackboneProvider,
    CodexTask,
)
from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    collect_allowed_refs_from_artifacts,
    detect_forbidden_biomedical_claims,
    detect_protocol_or_synthesis_text,
    detect_unbacked_citations,
)
from molecule_ranker.codex_backbone.parser import parse_codex_json
from molecule_ranker.codex_backbone.prompts import build_codex_prompt, render_task_template
from molecule_ranker.codex_backbone.runner import (
    CodexCLIRunner,
    CodexCommandBuilder,
    CodexRunner,
    CodexRunnerResult,
)
from molecule_ranker.codex_backbone.schemas import CodexTaskResult


class FakeRunner:
    def __init__(self, result: CodexRunnerResult | None = None) -> None:
        self.result = result or CodexRunnerResult(
            stdout='{"summary": "ok", "follow_up_tasks": []}',
            stderr="",
            return_code=0,
        )
        self.run_called = False

    def build_command(self, task: CodexTask, config: CodexBackboneConfig) -> list[str]:
        return [config.codex_cli_command, "exec", "--json"]

    def run(
        self,
        command: list[str],
        *,
        prompt: str,
        cwd: Path,
        timeout_seconds: int,
    ) -> CodexRunnerResult:
        self.run_called = True
        return self.result


def test_codex_task_schema_validation(tmp_path: Path) -> None:
    task = _task(tmp_path)

    assert task.task_type == "summarize_run"
    assert task.expected_output_format == "json"
    with pytest.raises(ValidationError):
        CodexTask.model_validate(
            {
                "task_id": "bad",
                "task_type": "invent_evidence",
                "prompt": "Do a task.",
                "working_directory": str(tmp_path),
                "input_artifact_paths": [],
                "allowed_commands": [],
                "forbidden_commands": [],
                "expected_output_format": "json",
                "timeout_seconds": 30,
                "require_json": True,
                "metadata": {},
            }
        )
    with pytest.raises(ValidationError):
        CodexTask.model_validate(
            {
                "task_id": "bad-format",
                "task_type": "summarize_run",
                "prompt": "Do a task.",
                "working_directory": str(tmp_path),
                "input_artifact_paths": [],
                "allowed_commands": [],
                "forbidden_commands": [],
                "expected_output_format": "xml",
                "timeout_seconds": 30,
                "require_json": True,
                "metadata": {},
            }
        )


def test_output_guardrail_allows_negated_research_limitations() -> None:
    text = (
        "No claims are made about cure, treatment, binding, activity, safety, "
        "synthesizability, dosing, or clinical use."
    )

    assert detect_forbidden_biomedical_claims(text) == []
    assert detect_forbidden_biomedical_claims("Aspirin treats disease.") == [
        "Forbidden biomedical claim: unsupported cure/treatment/prevention claim."
    ]


def test_disabled_provider_returns_disabled_result(tmp_path: Path) -> None:
    runner = FakeRunner()
    provider = CodexBackboneProvider(CodexBackboneConfig(), runner=runner)

    result = provider.run_task(_task(tmp_path))

    assert result.status == "disabled"
    assert runner.run_called is False


def test_dry_run_does_not_invoke_subprocess(tmp_path: Path) -> None:
    runner = FakeRunner()
    provider = CodexBackboneProvider(
        CodexBackboneConfig(enable_codex_backbone=True, codex_dry_run=True),
        runner=runner,
    )

    result = provider.run_task(_task(tmp_path))

    assert result.status == "succeeded"
    assert result.output_json is not None
    assert result.output_json["dry_run"] is True
    assert runner.run_called is False


def test_runner_timeout_behavior(tmp_path: Path) -> None:
    result = CodexRunner().run(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        prompt="",
        cwd=tmp_path,
        timeout_seconds=1,
    )

    assert result.timed_out is True
    assert result.return_code is None


def test_codex_command_builder_builds_safe_configurable_command(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        metadata={
            "codex_prompt_mode": "file",
            "codex_exec_subcommand": "exec",
            "codex_non_interactive_flag": "--non-interactive",
            "codex_extra_args": ["--some-future-flag"],
        },
    )
    command = CodexCommandBuilder().build(
        task,
        CodexBackboneConfig(
            enable_codex_backbone=True,
            codex_cli_command="/usr/local/bin/codex",
            codex_model="gpt-test",
            codex_reasoning_effort="high",
        ),
        prompt_file=tmp_path / "prompt.txt",
    )

    assert command.command == [
        "/usr/local/bin/codex",
        "--model",
        "gpt-test",
        "--reasoning-effort",
        "high",
        "--non-interactive",
        "exec",
        str(tmp_path / "prompt.txt"),
        "--json",
        "--some-future-flag",
    ]
    assert command.prompt_via_stdin is False


def test_runner_does_not_use_shell_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> object:
        observed["shell"] = kwargs.get("shell")

        class Completed:
            stdout = '{"summary": "ok"}'
            stderr = ""
            returncode = 0

        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = CodexCLIRunner().run(
        ["codex", "exec", "--json"],
        prompt="prompt",
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.return_code == 0
    assert observed["shell"] is False


def test_forbidden_command_detected_in_prompt_and_allowed_commands(tmp_path: Path) -> None:
    builder = CodexCommandBuilder()
    prompt_task = _task(tmp_path, prompt="Run sudo cat .env")
    command_task = _task(tmp_path, allowed_commands=["git push origin main"])

    prompt_warnings = builder.validate_task_commands(
        prompt_task,
        CodexBackboneConfig(enable_codex_backbone=True),
    )
    command_warnings = builder.validate_task_commands(
        command_task,
        CodexBackboneConfig(
            enable_codex_backbone=True,
            codex_allow_shell_commands=True,
        ),
    )

    assert any("sudo" in warning for warning in prompt_warnings)
    assert any("cat .env" in warning for warning in prompt_warnings)
    assert any("git push" in warning for warning in command_warnings)


def test_missing_codex_cli_fails_clearly(tmp_path: Path) -> None:
    result = CodexCLIRunner().run(
        [str(tmp_path / "missing-codex-binary")],
        prompt="prompt",
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.return_code == 127
    assert "Codex CLI unavailable" in result.stderr


def test_runner_dry_run_returns_command_and_redacted_prompt(tmp_path: Path) -> None:
    task = _task(tmp_path, prompt="Summarize. api_key=secretvalue123")
    result = CodexCLIRunner().run_task(
        task,
        CodexBackboneConfig(enable_codex_backbone=True, codex_dry_run=True),
        prompt="Prompt with token=secretvalue123",
        cwd=tmp_path,
    )

    assert result.dry_run is True
    assert result.return_code == 0
    assert "secretvalue123" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_json_parse_success(tmp_path: Path) -> None:
    runner = FakeRunner(
        CodexRunnerResult(
            stdout='{"summary": "ok", "follow_up_tasks": [], "limitations": []}',
            stderr="",
            return_code=0,
        )
    )
    provider = CodexBackboneProvider(
        CodexBackboneConfig(enable_codex_backbone=True),
        runner=runner,
    )

    result = provider.run_task(_task(tmp_path))

    assert result.status == "succeeded"
    assert result.output_json == {"summary": "ok", "follow_up_tasks": [], "limitations": []}


def test_json_parse_success_from_codex_jsonl_event_stream() -> None:
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"turn.started"}',
            (
                '{"type":"item.completed","item":{"id":"item-1","type":"agent_message",'
                '"text":"{\\"summary\\":\\"ok\\",\\"artifact_refs\\":[\\"artifact.json\\"]}"}}'
            ),
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
        ]
    )

    assert parse_codex_json(output) == {
        "summary": "ok",
        "artifact_refs": ["artifact.json"],
    }


def test_json_parse_failure(tmp_path: Path) -> None:
    runner = FakeRunner(CodexRunnerResult(stdout="not json", stderr="", return_code=0))
    provider = CodexBackboneProvider(
        CodexBackboneConfig(enable_codex_backbone=True),
        runner=runner,
    )

    result = provider.run_task(_task(tmp_path))

    assert result.status == "parse_failed"
    assert "JSON" in result.stderr


def test_secret_redaction(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("api_key=supersecretvalue\nnormal=data\n")
    runner = FakeRunner()
    provider = CodexBackboneProvider(
        CodexBackboneConfig(enable_codex_backbone=True, codex_dry_run=True),
        runner=runner,
    )
    task = _task(
        tmp_path,
        prompt="Summarize this. token=secretvalue123",
        input_artifact_paths=[str(artifact)],
    )

    result = provider.run_task(task)

    assert result.status == "succeeded"
    assert "supersecretvalue" not in result.output_text
    assert "secretvalue123" not in result.output_text
    assert "[REDACTED]" in result.output_text


def test_guardrails_flag_fake_pmid() -> None:
    warnings = detect_unbacked_citations(
        "This summary cites PMID:99999999.",
        allowed_citation_ids={"PMID:123456"},
    )

    assert warnings == ["Unbacked citation reference: PMID:99999999."]


def test_allowed_citation_ids_extracted_from_structured_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps({"citations": [{"pmid": "123456", "doi": "10.1234/abc"}]}))

    _artifact_refs, citation_ids = collect_allowed_refs_from_artifacts([str(artifact)])

    assert "PMID:123456" in citation_ids
    assert "10.1234/abc" in citation_ids


def test_guardrails_flag_fake_assay_result() -> None:
    result = _result("Candidate A has IC50 = 12 nM in the assay.")

    checked = check_output(result, allowed_artifact_refs=set(), allowed_citation_ids=set())

    assert checked.status == "guardrail_failed"
    assert any("Unbacked assay result" in warning for warning in checked.guardrail_warnings)


def test_guardrails_flag_synthesis_route() -> None:
    warnings = detect_protocol_or_synthesis_text(
        "A synthesis route uses reagents in solvent and heat to improve yield."
    )

    assert any("synthesis route" in warning for warning in warnings)
    assert any("operational synthesis detail" in warning for warning in warnings)


def test_guardrails_flag_dosing_instruction() -> None:
    warnings = detect_protocol_or_synthesis_text("Use human dosing at 10 mg/kg.")

    assert any("dosing" in warning.lower() for warning in warnings)


def test_guardrails_flag_generated_molecule_direct_evidence_claim() -> None:
    result = _result("Generated molecule GEN-MAOB-001 has direct experimental evidence.")

    checked = check_output(
        result,
        allowed_artifact_refs={"GEN-MAOB-001"},
        allowed_citation_ids=set(),
    )

    assert checked.status == "guardrail_failed"
    assert any("direct-evidence" in warning for warning in checked.guardrail_warnings)


def test_guardrails_grounded_summary_passes() -> None:
    result = _result(
        "The run summary cites PMID:123456 and describes limitations from artifact run-1."
    )

    checked = check_output(
        result,
        allowed_artifact_refs={"run-1"},
        allowed_citation_ids={"PMID:123456"},
    )

    assert checked.status == "succeeded"
    assert checked.guardrail_warnings == []


def test_model_codex_guardrails_flag_fake_metric(tmp_path: Path) -> None:
    artifact = _model_artifact(tmp_path)
    artifact_refs, citation_ids = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        json.dumps(
            {
                "status": "ok",
                "summary": "accuracy: 0.99 for model_id model-1",
                "model_id": "model-1",
                "dataset_id": "dataset-1",
                "training_run_id": "training-run-1",
                "evaluation_id": "evaluation-1",
                "prediction_batch_artifact_id": "batch-1",
            },
            sort_keys=True,
        ),
        task_type="explain_model_metrics",
    )

    checked = check_output(result, artifact_refs, citation_ids)

    assert checked.status == "guardrail_failed"
    assert any("Unbacked model metric" in warning for warning in checked.guardrail_warnings)


def test_model_codex_guardrails_flag_ungrounded_prediction(tmp_path: Path) -> None:
    artifact = _model_artifact(tmp_path)
    artifact_refs, citation_ids = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        json.dumps(
            {
                "status": "ok",
                "summary": "prediction_id: prediction-999 is favorable.",
                "model_id": "model-1",
                "dataset_id": "dataset-1",
                "training_run_id": "training-run-1",
                "evaluation_id": "evaluation-1",
                "prediction_batch_artifact_id": "batch-1",
            },
            sort_keys=True,
        ),
        task_type="explain_prediction_batch",
    )

    checked = check_output(result, artifact_refs, citation_ids)

    assert checked.status == "guardrail_failed"
    assert any(
        "Ungrounded model prediction field" in warning for warning in checked.guardrail_warnings
    )


def test_model_codex_guardrails_allow_safe_grounded_summary(tmp_path: Path) -> None:
    artifact = _model_artifact(tmp_path)
    artifact_refs, citation_ids = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        json.dumps(
            {
                "status": "ok",
                "summary": (
                    "Model model-1 uses dataset dataset-1; training_run_id training-run-1 "
                    "and evaluation_id evaluation-1 report accuracy: 0.75. "
                    "Prediction batch artifact batch-1 contains prediction_id prediction-1. "
                    "Predictions are not evidence and not assay results."
                ),
                "model_id": "model-1",
                "dataset_id": "dataset-1",
                "training_run_id": "training-run-1",
                "evaluation_id": "evaluation-1",
                "prediction_batch_artifact_id": "batch-1",
                "limitations": ["Predictions are not evidence."],
            },
            sort_keys=True,
        ),
        task_type="summarize_model_card",
    )

    checked = check_output(result, artifact_refs, citation_ids)

    assert checked.status == "succeeded"
    assert checked.guardrail_warnings == []


def test_prompt_templates_include_safety_constraints(tmp_path: Path) -> None:
    for task_type in [
        "summarize_run",
        "explain_ranking",
        "compare_candidates",
        "plan_followup_run",
        "draft_dossier",
        "engineering_test_loop",
    ]:
        task = _task(tmp_path, task_type=task_type)
        payload = json.loads(
            build_codex_prompt(task, CodexBackboneConfig()).prompt_text
        )
        instructions = " ".join(payload["instructions"])

        assert "Do not invent evidence" in instructions
        assert "Use only provided artifacts" in instructions
        assert "No medical advice" in instructions
        assert "No synthesis/lab protocols" in instructions
        assert "No unsupported claims" in instructions


def test_prompt_templates_request_json(tmp_path: Path) -> None:
    task = _task(tmp_path, task_type="compare_candidates")
    payload = json.loads(build_codex_prompt(task, CodexBackboneConfig()).prompt_text)

    assert payload["expected_output_format"] == "json"
    assert payload["require_json"] is True
    assert "Return valid JSON only." in payload["instructions"]
    assert payload["output_json_schema"] == render_task_template("compare_candidates")[
        "output_json_schema"
    ]


def test_prompt_templates_include_artifact_grounding_instructions(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Run report content.")
    task = _task(tmp_path, task_type="summarize_run", input_artifact_paths=[str(report)])

    payload = json.loads(build_codex_prompt(task, CodexBackboneConfig()).prompt_text)
    instructions = " ".join(payload["instructions"])

    assert "Use only provided artifacts as factual sources." in instructions
    assert "Cite artifact IDs or file paths" in instructions
    assert payload["template"]["required_inputs"] == ["report.md", "candidates.json", "trace.json"]
    assert payload["artifacts"][0]["path"] == str(report.resolve())


def test_model_prompt_templates_include_model_boundaries(tmp_path: Path) -> None:
    task = _task(tmp_path, task_type="summarize_model_card")
    payload = json.loads(build_codex_prompt(task, CodexBackboneConfig()).prompt_text)
    instructions = " ".join(payload["instructions"])

    assert "Codex is limited to artifact summarization and debugging" in instructions
    assert "Codex cannot invent metrics" in instructions
    assert "Codex cannot change model cards" in instructions
    assert "prediction_batch_artifact_id" in instructions


def _task(
    tmp_path: Path,
    *,
    task_type: str = "summarize_run",
    prompt: str = "Summarize the run artifact.",
    input_artifact_paths: list[str] | None = None,
    allowed_commands: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> CodexTask:
    return CodexTask(
        task_id="task-1",
        task_type=task_type,  # type: ignore[arg-type]
        prompt=prompt,
        working_directory=str(tmp_path),
        input_artifact_paths=input_artifact_paths or [],
        allowed_commands=allowed_commands or [],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=30,
        require_json=True,
        metadata=metadata or {},
    )


def _result(text: str, *, task_type: str = "summarize_run") -> CodexTaskResult:
    return CodexTaskResult(
        task_id="task-1",
        task_type=task_type,  # type: ignore[arg-type]
        status="succeeded",
        output_text=text,
        stdout=text,
        stderr="",
        return_code=0,
    )


def _model_artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "model_bundle.json"
    artifact.write_text(
        json.dumps(
            {
                "model_id": "model-1",
                "training_dataset_id": "dataset-1",
                "training_run_id": "training-run-1",
                "evaluation_id": "evaluation-1",
                "batch_id": "batch-1",
                "metrics": {"accuracy": 0.75},
                "calibration_metrics": {"brier": 0.2, "status": "uncalibrated"},
                "predictions": [
                    {
                        "prediction_id": "prediction-1",
                        "candidate_name": "Candidate 1",
                        "endpoint_id": "endpoint-binary",
                        "predicted_probability": 0.6,
                        "prediction_label": "surrogate_positive",
                        "uncertainty": 0.4,
                        "confidence": 0.6,
                        "applicability_domain": "near_domain",
                        "calibration_status": "uncalibrated",
                    }
                ],
            },
            sort_keys=True,
        )
    )
    return artifact
