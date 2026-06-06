# Agent Runtime

V3 uses a governed Codex runtime and default subagent orchestration for the
`full_discovery_loop`.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Default Subagents

Default orchestration includes:

- `ProgramManagerSubagent`
- `EvidenceReviewerSubagent`
- `DevelopabilitySafetySubagent`
- `GraphReasonerSubagent`
- `HypothesisPlannerSubagent`
- `PortfolioStrategistSubagent`
- `CampaignPlannerSubagent`
- `EvaluationValidatorSubagent`
- `GuardrailSentinelSubagent`
- `PlatformOperatorSubagent`

Optional subagents join only when enabled:

- `MoleculeDesignerSubagent` when generation is enabled.
- `BiologicsEngineerSubagent` when biologics is enabled.
- `IntegrationOperatorSubagent` when integrations are enabled.

## Runtime Rules

- `ProgramManagerSubagent` coordinates the workflow.
- `EvidenceReviewerSubagent` validates source-backed evidence.
- `GuardrailSentinelSubagent` reviews the final bundle.
- Generated outputs require review gates.
- `CampaignPlannerSubagent` cannot activate campaigns.
- Codex planning must use approved tools only.

## Autonomy Levels

- `observe_only`: collect status and traces.
- `suggest_only`: propose actions without execution.
- `execute_safe_tools`: execute approved safe deterministic tools.
- `execute_with_approval`: default governed execution.
- `supervised_auto`: requires explicit governance configuration.

## Interpreting Agent Activity

Agent activity is operational context. It can explain how a bundle was produced,
which tools ran, and where approvals are needed. It is not scientific evidence.

