from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.artifact_context import summarize_large_artifact
from molecule_ranker.codex_backbone.guardrails import is_secret_path, redact_secrets
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask

SYSTEM_LIMITATIONS = [
    "Codex CLI is the LLM orchestration backbone, not a biomedical source of truth.",
    "Use only supplied artifacts and molecule-ranker command outputs as factual sources.",
    "Do not invent targets, molecules, assay results, citations, evidence, or scores.",
    "Do not directly alter scores; call molecule-ranker scoring modules instead.",
    "Do not claim cure, treatment, binding, activity, safety, or synthesizability.",
    "Do not provide synthesis routes, lab protocols, dosing, or patient treatment instructions.",
    "Do not read credentials, secrets, or private keys into prompts.",
]

ARTIFACT_GROUNDING_INSTRUCTIONS = [
    "Use only provided artifacts as factual sources.",
    "Cite artifact IDs or file paths for every factual claim.",
    "If an artifact is missing or does not support a point, state that the evidence is missing.",
    "Do not use outside biomedical knowledge to fill gaps.",
]

JSON_OUTPUT_INSTRUCTIONS = [
    "Return valid JSON only.",
    "Do not wrap JSON in Markdown fences.",
    "Use the exact top-level keys requested by the template.",
]

COMMON_SAFETY_CONSTRAINTS = [
    "Do not invent evidence.",
    "Do not invent citations, PMIDs, DOIs, molecules, targets, assay results, or scores.",
    "Use only provided artifacts.",
    "Cite artifact IDs or file paths.",
    "No medical advice.",
    "No synthesis/lab protocols.",
    "No unsupported claims.",
    "No claims of cure, treatment, safety, efficacy, binding, activity, or synthesizability.",
    "Do not invent registry IDs, Benchling IDs, external records, assay runs, or assay results.",
    "Do not activate mappings, enqueue sync jobs, or write to external systems.",
    "Codex outputs must cite internal artifact IDs and external record refs supplied in context.",
]

TEMPLATE_ALIASES = {
    "draft_dossier": "draft_dossier_summary",
}

TASK_TEMPLATES: dict[str, dict[str, Any]] = {
    "summarize_run": {
        "description": "Summarize one molecule-ranker run for expert review.",
        "required_inputs": ["report.md", "candidates.json", "trace.json"],
        "optional_inputs": [
            "generated_candidates.json",
            "developability.json",
            "experimental_evidence.json",
        ],
        "instructions": [
            "Summarize the run at a high level without adding biomedical facts.",
            "Top candidates must come from candidates.json or report.md.",
            "Warnings must include key limitations and missing-artifact caveats.",
        ],
        "output_json_schema": {
            "summary": "string",
            "top_candidates": ["artifact-backed candidate summaries"],
            "main_uncertainties": ["uncertainty or evidence-gap strings"],
            "warnings": ["warning strings"],
            "artifact_refs": ["artifact IDs or file paths used"],
        },
    },
    "explain_ranking": {
        "description": "Explain why a candidate is ranked where it is.",
        "required_inputs": ["candidate record", "score breakdown", "evidence summaries"],
        "optional_inputs": [],
        "instructions": [
            "Explain ranking factors from the provided candidate and score fields only.",
            "Separate evidence factors from risk factors.",
            "List what is explicitly not claimed by the ranking.",
        ],
        "output_json_schema": {
            "candidate_name": "string",
            "why_ranked_here": "string",
            "evidence_factors": ["artifact-backed evidence factor strings"],
            "risk_factors": ["artifact-backed risk factor strings"],
            "not_claimed": ["unsupported claims that are explicitly not made"],
        },
    },
    "compare_candidates": {
        "description": "Compare two or more candidates for review triage.",
        "required_inputs": ["candidate records", "score breakdowns", "evidence summaries"],
        "optional_inputs": ["review comments", "developability summaries"],
        "instructions": [
            "Do not select a biomedical winner unless the artifacts explicitly do so.",
            "Report similarities, differences, risks, and review questions.",
        ],
        "output_json_schema": {
            "comparison_summary": "string",
            "shared_strengths": ["shared artifact-backed strengths"],
            "differences": ["artifact-backed differences"],
            "risks": ["risk or limitation strings"],
            "review_questions": ["high-level expert review questions"],
        },
    },
    "plan_followup_run": {
        "description": "Plan safe computational follow-up actions.",
        "required_inputs": ["run artifacts or review artifacts"],
        "optional_inputs": ["active_learning_batch.json", "experimental_evidence.json"],
        "instructions": [
            "Recommend only high-level computational or review actions.",
            "Safe CLI commands must be molecule-ranker commands or allowed engineering commands.",
            "Do not include wet-lab protocols, synthesis steps, or dosing instructions.",
        ],
        "output_json_schema": {
            "recommended_actions": [
                {
                    "action_type": (
                        "rerun_literature|stricter_developability|active_learning|review|"
                        "experiment_import"
                    ),
                    "rationale": "string",
                    "safe_cli_command": "string",
                }
            ],
            "limitations": ["limitation strings"],
        },
    },
    "draft_dossier_summary": {
        "description": "Draft a concise dossier summary from existing review/run evidence.",
        "required_inputs": ["candidate record", "evidence summaries", "risk summaries"],
        "optional_inputs": ["review decisions", "comments", "experimental summaries"],
        "instructions": [
            "Keep this as a summary for expert review, not a clinical dossier.",
            "Validation questions must be non-operational and high level.",
        ],
        "output_json_schema": {
            "executive_summary": "string",
            "key_evidence": ["artifact-backed evidence strings"],
            "key_risks": ["risk or limitation strings"],
            "validation_questions": ["high-level validation questions"],
        },
    },
    "generate_review_questions": {
        "description": "Draft bounded expert review questions from existing review artifacts.",
        "required_inputs": ["review item record", "dossier sections", "evidence summaries"],
        "optional_inputs": ["review comments", "experimental summaries"],
        "instructions": [
            "Generate only high-level review questions for human reviewers.",
            "Do not draft final reviewer decisions or recommend clinical actions.",
            "For generated molecules, preserve the no-direct-evidence warning.",
        ],
        "output_json_schema": {
            "candidate_name": "string",
            "review_questions": ["high-level review question strings"],
            "uncertainty_questions": ["evidence-gap question strings"],
            "not_claimed": ["unsupported claims that are explicitly not made"],
            "artifact_refs": ["artifact IDs or file paths used"],
        },
    },
    "explain_conflicting_evidence": {
        "description": "Explain conflicts or limitations in existing evidence summaries.",
        "required_inputs": ["review item record", "evidence summaries"],
        "optional_inputs": ["literature summaries", "experimental summaries", "review comments"],
        "instructions": [
            "Describe conflicts only when they are present in the supplied artifacts.",
            "Separate observed conflicts from missing or weak evidence.",
            "Do not resolve conflicts as final biomedical truth.",
        ],
        "output_json_schema": {
            "candidate_name": "string",
            "conflict_summary": "string",
            "conflicting_factors": ["artifact-backed conflict or limitation strings"],
            "missing_evidence": ["missing-evidence strings"],
            "artifact_refs": ["artifact IDs or file paths used"],
        },
    },
    "summarize_experimental_results": {
        "description": "Summarize imported experimental result summaries for review.",
        "required_inputs": ["review item record", "experimental evidence summary"],
        "optional_inputs": ["assay-result links", "review suggestions"],
        "instructions": [
            "Summarize only imported experimental summaries already present in artifacts.",
            "Do not create assay results or infer clinical efficacy from in-vitro results.",
            "Do not provide experimental protocols or operating conditions.",
        ],
        "output_json_schema": {
            "candidate_name": "string",
            "experimental_summary": "string",
            "result_context": ["artifact-backed result-context strings"],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs or file paths used"],
        },
    },
    "engineering_test_loop": {
        "description": "Diagnose engineering checks and plan a safe test loop.",
        "required_inputs": ["test output, lint output, typecheck output, or source snippets"],
        "optional_inputs": ["git diff", "trace logs"],
        "instructions": [
            "Focus on engineering automation only.",
            "Commands to run must be limited to allowed engineering commands.",
            "Do not inspect secrets or print environment variables.",
        ],
        "output_json_schema": {
            "diagnosis": "string",
            "proposed_fix": "string",
            "commands_to_run": ["safe commands"],
            "files_to_inspect": ["relative or absolute file paths"],
        },
    },
    "suggest_schema_mapping": {
        "description": "Suggest candidate field mappings from external records to data contracts.",
        "required_inputs": ["integration context artifact with records and data contract"],
        "optional_inputs": ["existing mappings", "validation errors"],
        "instructions": [
            "Suggest mappings only from fields present in the supplied artifacts.",
            "Every suggestion must be status=pending_review.",
            "Do not activate mappings or invent external IDs, registry IDs, Benchling IDs, "
            "fields, or records.",
            "Cite artifact_refs and external_record_refs for every mapping suggestion.",
        ],
        "output_json_schema": {
            "suggested_mappings": ["pending mapping suggestion objects"],
            "validation_notes": ["deterministic validation notes"],
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "explain_sync_failure": {
        "description": "Explain integration sync failures from existing sync records.",
        "required_inputs": ["sync job", "sync records", "connector audit summaries"],
        "optional_inputs": ["validation errors", "warnings"],
        "instructions": [
            "Explain only failures present in supplied sync records or audit metadata.",
            "Do not invent external records, assay results, or connector behavior.",
            "Cite sync_record_ids, artifact_refs, and external_record_refs.",
        ],
        "output_json_schema": {
            "failure_summary": "string",
            "failed_records": ["sync-record-grounded failure strings"],
            "likely_causes": ["artifact-backed cause strings"],
            "next_review_questions": ["safe review questions"],
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "summarize_external_record": {
        "description": "Summarize one external record from artifact-scoped metadata.",
        "required_inputs": ["external record payload artifact"],
        "optional_inputs": ["data contract", "mapping context"],
        "instructions": [
            "Summarize only supplied external record fields.",
            "Do not infer missing assay results, registry IDs, or Benchling IDs.",
            "Cite artifact_refs and external_record_refs.",
        ],
        "output_json_schema": {
            "record_summary": "string",
            "key_fields": ["field summary strings"],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "suggest_mapping_review_questions": {
        "description": "Suggest human review questions for pending integration mappings.",
        "required_inputs": ["pending mapping", "external record refs", "deterministic signals"],
        "optional_inputs": ["conflict details"],
        "instructions": [
            "Ask questions only; do not approve, reject, or activate mappings.",
            "Do not invent external IDs or records.",
            "Cite artifact_refs and external_record_refs.",
        ],
        "output_json_schema": {
            "review_questions": ["mapping review question strings"],
            "blocking_uncertainties": ["uncertainty strings"],
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "draft_export_summary": {
        "description": "Draft a safe summary for a planned export artifact.",
        "required_inputs": ["export artifact preview", "target system metadata"],
        "optional_inputs": ["data contract validation report"],
        "instructions": [
            "Draft a summary only; do not write to external systems.",
            "Do not include secrets, credentials, protocols, synthesis steps, dosing, or "
            "treatment guidance.",
            "Cite artifact_refs and external_record_refs.",
        ],
        "output_json_schema": {
            "export_summary": "string",
            "records_in_scope": ["artifact-backed record strings"],
            "write_boundary": "string",
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "compare_internal_external_record": {
        "description": "Compare an internal entity record with one external record.",
        "required_inputs": ["internal record", "external record", "mapping context"],
        "optional_inputs": ["data contract validation report"],
        "instructions": [
            "Compare only supplied fields and deterministic identifiers.",
            "Do not invent IDs, assay results, or evidence.",
            "Do not create EvidenceItem or activate mappings.",
            "Cite artifact_refs and external_record_refs.",
        ],
        "output_json_schema": {
            "comparison_summary": "string",
            "matching_fields": ["field strings"],
            "mismatches": ["field strings"],
            "review_questions": ["safe review questions"],
            "artifact_refs": ["artifact IDs or file paths used"],
            "external_record_refs": ["external record refs used"],
        },
    },
    "summarize_project": {
        "description": "Summarize a molecule-ranker project from manifests and run summaries.",
        "required_inputs": ["project Codex input JSON with artifact manifest and run summaries"],
        "optional_inputs": [],
        "instructions": [
            "Use only artifact manifests and run summaries supplied in the project input.",
            "Do not create, modify, or reinterpret scientific evidence.",
            "Project highlights must cite artifact IDs.",
        ],
        "output_json_schema": {
            "project_summary": "string",
            "run_highlights": ["artifact-backed run summary strings"],
            "main_uncertainties": ["uncertainty strings"],
            "artifact_refs": ["artifact IDs used"],
        },
    },
    "explain_run_changes": {
        "description": "Explain changes between registered project runs.",
        "required_inputs": ["project Codex input JSON with run summaries and artifact manifest"],
        "optional_inputs": ["project comparison JSON"],
        "instructions": [
            "Explain changes only from registered run summaries and comparison artifacts.",
            "Do not infer biomedical causes for score or candidate differences.",
            "Cite artifact IDs for every run-change statement.",
        ],
        "output_json_schema": {
            "change_summary": "string",
            "run_differences": ["artifact-backed differences"],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
    },
    "draft_project_update": {
        "description": "Draft a project update from existing project artifacts.",
        "required_inputs": ["project Codex input JSON with run summaries and artifact manifest"],
        "optional_inputs": [],
        "instructions": [
            "Write a status update for reviewers, not a scientific evidence item.",
            "Keep claims limited to artifact-backed workflow status.",
            "Cite artifact IDs for status statements.",
        ],
        "output_json_schema": {
            "project_update": "string",
            "evidence_status": ["artifact-backed status strings"],
            "risks": ["risk or limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
    },
    "suggest_next_project_actions": {
        "description": "Suggest safe next project actions from existing project artifacts.",
        "required_inputs": ["project Codex input JSON with run summaries and artifact manifest"],
        "optional_inputs": ["project comparison JSON"],
        "instructions": [
            "Recommend only computational, review, comparison, or import actions.",
            "Safe CLI commands must be molecule-ranker commands.",
            "Do not suggest wet-lab protocols, synthesis steps, dosing, or treatment actions.",
        ],
        "output_json_schema": {
            "recommended_actions": [
                {
                    "action_type": (
                        "review|rerun|compare|summarize|experiment_import|active_learning"
                    ),
                    "rationale": "string",
                    "safe_cli_command": "string",
                }
            ],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
    },
}


@dataclass
class PromptBundle:
    prompt_text: str
    artifacts_read: list[str] = field(default_factory=list)
    guardrail_warnings: list[str] = field(default_factory=list)


def render_task_template(task_type: str) -> dict[str, Any]:
    key = TEMPLATE_ALIASES.get(task_type, task_type)
    template = TASK_TEMPLATES.get(key)
    if template is None:
        return {
            "description": "General bounded artifact-inspection task.",
            "required_inputs": ["provided artifacts"],
            "optional_inputs": [],
            "instructions": [
                "Answer only from provided artifacts.",
                "Return the requested format.",
            ],
            "output_json_schema": {"summary": "string", "limitations": ["strings"]},
        }
    return template


def build_codex_prompt(task: CodexTask, config: CodexBackboneConfig) -> PromptBundle:
    warnings: list[str] = []
    artifacts = []
    artifacts_read: list[str] = []
    for artifact_path in task.input_artifact_paths:
        path = Path(artifact_path)
        if is_secret_path(path):
            warnings.append(f"Skipped secret-like artifact path: {artifact_path}")
            continue
        if not path.exists() or not path.is_file():
            warnings.append(f"Skipped missing artifact: {artifact_path}")
            continue
        data = path.read_bytes()
        text = summarize_large_artifact(path, config.codex_max_artifact_bytes)
        if text.startswith("[EXCLUDED:"):
            warnings.append(f"Skipped excluded artifact: {artifact_path}")
            continue
        truncated = text.startswith("[TRUNCATED:")
        if config.codex_redact_secrets:
            text = redact_secrets(text)
        artifacts.append(
            {
                "path": str(path.resolve()),
                "size_bytes": len(data),
                "truncated": truncated,
                "content": text,
            }
        )
        artifacts_read.append(str(path.resolve()))

    user_prompt = redact_secrets(task.prompt) if config.codex_redact_secrets else task.prompt
    template = render_task_template(str(task.task_type))
    payload: dict[str, Any] = {
        "role": "molecule-ranker Codex CLI backbone provider",
        "task_id": task.task_id,
        "task_type": task.task_type,
        "template": template,
        "instructions": [
            *SYSTEM_LIMITATIONS,
            *ARTIFACT_GROUNDING_INSTRUCTIONS,
            *JSON_OUTPUT_INSTRUCTIONS,
            *COMMON_SAFETY_CONSTRAINTS,
            *template.get("instructions", []),
        ],
        "user_prompt": user_prompt,
        "expected_output_format": task.expected_output_format,
        "require_json": task.require_json,
        "output_json_schema": template.get("output_json_schema", {}),
        "allowed_commands": (
            [*config.codex_allowed_commands, *task.allowed_commands]
            if config.codex_allow_shell_commands
            else []
        ),
        "forbidden_commands": [*config.codex_forbidden_commands, *task.forbidden_commands],
        "artifacts": artifacts,
        "metadata": task.metadata,
    }
    return PromptBundle(
        prompt_text=json.dumps(payload, indent=2, sort_keys=True),
        artifacts_read=artifacts_read,
        guardrail_warnings=warnings,
    )
