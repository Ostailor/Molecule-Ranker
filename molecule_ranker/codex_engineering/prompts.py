from __future__ import annotations

import json

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.codex_engineering.schemas import CodexEngineeringTask

ENGINEERING_GUARDRAILS = [
    "This is codebase engineering work only.",
    "Do not fabricate biomedical data, targets, molecules, assay results, citations, or evidence.",
    "Do not interpret molecule-ranker artifacts as biomedical truth.",
    "Inspect code, tests, docs, logs, and benchmark outputs only as engineering artifacts.",
    "Do not edit files unless explicit apply mode is enabled.",
    "Do not publish to remote git repositories unless explicit permission is enabled.",
    "Do not propose or run deletion commands unless explicit deletion approval is enabled.",
    "Do not read .env files, credentials, API keys, tokens, private keys, or environment dumps.",
    "Do not include secrets in prompts, logs, patches, or reports.",
    "Return valid JSON only.",
]

TASK_INSTRUCTIONS = {
    "implementation_planning": [
        "Produce an implementation plan with files to inspect, likely changes, risks, and tests.",
    ],
    "bug_fix_planning": [
        "Diagnose likely causes and propose a focused bug-fix plan.",
    ],
    "test_failure_analysis": [
        "Analyze the supplied test output and identify likely failing areas.",
        "Recommend verification commands, but do not fabricate test results.",
    ],
    "patch_proposal": [
        "Propose a patch at a high level unless apply mode is enabled.",
        "Patch proposals must preserve existing behavior unless the goal explicitly changes it.",
    ],
    "docs_update_proposal": [
        "Plan documentation updates for the requested section.",
        "Do not invent project capabilities that are not present in the repository.",
    ],
    "migration_planning": [
        "Plan migration steps, compatibility risks, and verification.",
    ],
    "benchmark_failure_analysis": [
        "Analyze benchmark failure output as an engineering signal.",
        "Do not infer biomedical performance conclusions.",
    ],
}

OUTPUT_SCHEMA = {
    "diagnosis": "string",
    "proposed_plan": ["ordered engineering steps"],
    "files_to_inspect": ["paths"],
    "commands_to_run": ["safe local commands"],
    "risks": ["engineering risks"],
    "requires_apply": "boolean",
}


def render_engineering_prompt(task: CodexEngineeringTask) -> str:
    payload = {
        "role": "molecule-ranker Codex engineering automation",
        "task_id": task.task_id,
        "task_type": task.task_type,
        "goal": redact_secrets(task.goal),
        "working_directory": str(task.working_directory),
        "input_paths": [str(path) for path in task.input_paths],
        "log_text": redact_secrets(task.log_text or ""),
        "apply_enabled": task.apply,
        "allow_git_push": task.allow_git_push,
        "allow_deletions": task.allow_deletions,
        "guardrails": ENGINEERING_GUARDRAILS,
        "task_instructions": TASK_INSTRUCTIONS.get(task.task_type, []),
        "output_json_schema": OUTPUT_SCHEMA,
        "metadata": task.metadata,
    }
    return json.dumps(payload, indent=2, sort_keys=True)
