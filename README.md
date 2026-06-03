# molecule-ranker

`molecule-ranker` is a validated enterprise discovery operating system for
internal research teams running source-backed molecule ranking and research
operations. V2.1 is the controlled Codex runtime-agent release. It keeps the
V2.0 enterprise-stable platform intact and upgrades Codex CLI from guarded
summarization/orchestration into a runtime agent backbone that can perform
molecule-ranker tasks only through approved, audited, deterministic tools. V2.0
stabilized the V1.9 pilot-hardened platform for enterprise release with stable release
contracts, production-grade deployment, security hardening, enterprise identity
and access controls, tenant/project isolation, validation evidence packages,
operational runbooks, disaster recovery, governance and audit readiness,
performance and reliability targets, release certification workflows, stable
APIs and SDK, enterprise admin controls, full end-to-end synthetic demo
workflows, and V2.0 documentation and training. V2.1 does not add major new
science capabilities, and it keeps all scientific guardrails intact. V1.9
already added enterprise/internal pilot hardening. V1.8 already added scientific evaluation benchmark suites and
prospective validation analytics. V1.7 already added closed-loop campaign
planning and budget-aware execution management. V1.6 already added automated graph-backed hypothesis
generation and testable research-question planning. V1.5 already added a cross-program
knowledge graph and mechanism-level reasoning layer. V1.4 already added multi-objective portfolio
optimization and program-level decision analytics. V1.3 already added conservative
structure-based design and protein-ligand workflow hardening: auditable target structure selection,
externally prepared receptor and ligand artifact tracking, docking
reproducibility metadata, pose QC, consensus rescoring, interaction profiles,
structure-aware generated molecule filtering, report cards, and hosted
structure job guardrails. V1.2 already provides a formal predictive model
plugin interface, calibrated assay-specific surrogate model artifacts, and
hosted model training and validation job surfaces. The V2.1 capability set
therefore includes source-backed ranking, generated hypotheses,
developability, experiments, review, Codex backbone, integrations, models,
structure workflows, portfolios, graphs, hypotheses, campaigns, evaluations,
enterprise operations, and controlled Codex runtime tool execution. V1.1 already supports AgentGraph scientific design
workflows, generated-molecule report cards, uncertainty/diversity/readiness
triage, generator benchmarking, source-backed ranking, generated molecule
hypotheses, developability triage, literature evidence, experimental feedback,
review workflows, Codex-backed orchestration, hosted platform mode, and guarded
external integrations.

V2.1 is for internal research use only. It is not a regulated clinical product.
It does not provide medical advice, synthesis instructions, lab protocols,
dosing, or patient treatment guidance. It does not claim that molecules cure,
treat, are safe, bind, inhibit, activate, or are active. Docking scores are not
proof of binding, poses are not experimental evidence, structure-based scores
are not activity evidence, and predicted structures are lower-confidence than
suitable experimental structures. Portfolio recommendations are research
prioritization aids, not clinical or experimental instructions, and selected
molecules are not claimed safe, active, effective, or synthesizable. The
knowledge graph is a memory and reasoning layer, not a source of new biomedical
truth; graph-inferred relationships are hypotheses unless backed by source
evidence, and graph paths do not prove causality, efficacy, safety, binding, or
activity. Automated hypotheses are planning artifacts, not evidence; research
questions are not lab protocols, and validation plans are not experimental
procedures. Campaign plans are research-management artifacts, not lab
protocols, and budget-aware execution management does not provide synthesis
routes, reagents, concentrations, incubation times, temperatures, animal dosing,
human dosing, or patient treatment guidance. Benchmark results are evaluation
artifacts, not biomedical evidence, and prospective validation analytics are not
clinical validation. Codex is an
orchestration and summarization layer, not scientific truth; it may not invent
structures, poses, binding sites, docking scores, interactions, evidence, assay
results, citations, molecules, mechanisms, graph nodes, graph edges,
hypotheses, scores, portfolio optimization outputs, campaign metrics, campaign
costs, campaign outcomes, benchmark results, labels, metrics, conclusions, or
advancement decisions. Data provenance, audit logs, deterministic validation,
guardrails, contracts, and validation packages are core principles. Enterprise
validation artifacts are software/process validation artifacts, not clinical
validation, benchmark proof, prospective validation proof, assay results, or
molecule activity/safety/efficacy evidence.

Given a disease name, V2.1 resolves the disease through public biomedical data
sources, discovers evidence-backed targets, retrieves existing molecules linked
to those targets, retrieves real literature evidence, ranks molecules as
transparent research hypotheses, and can optionally generate
target-conditioned in-silico molecule hypotheses from retrieved structures.
Generated molecules are computational hypotheses only: they are not known
actives, gain direct experimental evidence only from exact linked imported
results for the tested structure, and are ranked separately from existing
evidence-backed molecules unless explicitly requested otherwise.

## Current Scope Through V2.1

V2.1 implements existing-molecule ranking, opt-in generated hypotheses,
developability-aware computational triage, expert review workflows, and an
experimental feedback loop from user-imported assay result files, with Codex CLI
available as a guarded orchestration layer, hosted-mode platform services, and
external integration primitives. V2.1 adds `CodexRuntimeAgent`, `ActionPlanner`,
`ToolRegistry`, `PolicyEngine`, `ApprovalGate`, `ActionExecutor`,
`ArtifactValidator`, `GuardrailChecker`, and `AuditLogger` so Codex can execute
multi-step molecule-ranker workflows only through approved deterministic tools.
V2.0 added enterprise release stabilization:
stable release contracts, production deployment, security hardening, identity
and access controls, tenant/project isolation, validation evidence packages,
operational runbooks, disaster recovery verification, governance and audit
readiness, performance and reliability targets, release certification
workflows, stable APIs and SDK, enterprise admin controls, end-to-end synthetic
demo workflows, and documentation/training material. V2.1 does not add major
new science capabilities, does not expand molecule generation, docking, ADMET,
graph reasoning, model training, integrations, or campaign planning except for
controlled runtime orchestration, stability, validation, security, and enterprise readiness, and preserves all
scientific guardrails. V1.9 added enterprise/internal pilot hardening:
usability polish, performance optimization, reliability hardening, operational
readiness, supportability, pilot onboarding, admin/support workflows, better
error messages, job retry/resume/cancel robustness, dashboard workflow
improvements, dataset/artifact migration safety, deployment diagnostics,
monitoring and alerting, pilot feedback capture, support bundle generation, and
pre-V2.0 readiness validation. V1.8 added scientific evaluation benchmarks,
prospective validation analytics, decision-quality reports, guardrail benchmark
runs, longitudinal performance trends, and reproducibility manifests. V1.7 added
deterministic closed-loop campaign
plans, budget fit, review-gated work packages, high-level assay/review/compute
slot allocation, replan triggers, expected learning value, opportunity cost,
candidate-batch tracking, memos, dashboards, provenance, and campaign audit
trails. V1.6 added automated hypothesis generation and testable research-question
planning over graph-backed artifacts. V1.5 added
cross-program graph memory and mechanism-level reasoning over existing artifacts. V1.4 added deterministic portfolio analytics for
program-level decisions: balanced candidate selection, review versus assay
triage queues, overrepresentation/underexploration summaries, learning-value
batch selection, correlated-risk deprioritization, scenario robustness, and
human-approval stage gates. V1.3 added advanced structure workflow
hardening while keeping all structure work optional and conservative. V1.2 added
a formal model plugin interface,
assay-specific local surrogate training from QC-passed imported results,
deterministic featurization manifests, leakage-aware splits, calibration and
applicability-domain metadata, model cards, training manifests, metrics,
prediction artifacts, hosted model jobs, and dashboard labeling that keeps model
predictions separate from experimental evidence and assay results. V1.1 added
deterministic AgentGraph orchestration for generated-molecule design planning,
target-conditioned objective metadata, seed/scaffold traceability,
ensemble-aware generation metadata, oracle scoring, medicinal chemistry
critique, uncertainty/diversity estimates, experiment-readiness triage,
active-learning design metadata, improved generated report cards, generator
benchmarking, and validation workflows.

- Resolve disease names to public biomedical disease entities with ambiguity handling.
- Retrieve real disease-associated targets with richer target identifiers and metadata.
- Retrieve existing molecules associated with those targets from ChEMBL mechanism,
  activity, assay, indication, and warning records where available.
- Retrieve PubMed literature records through NCBI E-utilities and extract
  citation-backed conservative claims from source-provided titles, abstracts,
  and snippets.
- Optionally enrich literature records with OpenAlex citation, open-access, and
  retraction metadata.
- Score candidates with a transparent component breakdown. Literature evidence
  is used as a research-prioritization modifier, not as proof of therapeutic
  efficacy and not as a replacement for database evidence.
- Generate target-conditioned novel candidate structures only when
  `--enable-generation` is passed or the `molecule-ranker generate` command is
  used. Generation is disabled by default.
- Use a V1.1 generator ensemble over real retrieved seed molecules, including
  SELFIES mutation/crossover, fragment growth, scaffold hopping, matched-pair
  transforms, and reactionless analog enumeration.
- Use RDKit for generated-structure validation, canonicalization, descriptors,
  fingerprints, similarity, and coarse chemistry filters.
- Assess physicochemical descriptors, drug-likeness heuristics, chemistry
  alerts, rule-based ADMET triage, toxicity-risk flags, synthetic-accessibility
  heuristics, synthesizability scoring, and chemical liability flags for
  existing and generated molecules when parseable structures are available.
- Apply a bounded developability adjustment to evidence-backed ranking scores.
- Optionally retrieve target structure metadata, select structures with an
  auditable policy that prefers suitable experimental structures, and optionally
  run docking only when explicitly enabled. Docking is disabled by default.
- Track externally prepared receptor artifacts, ligand 3D artifacts, binding-site
  selection methods, docking reproducibility metadata, pose QC, consensus
  rescoring, and protein-ligand interaction profiles without treating any of
  them as evidence.
- Emit structure-based report cards and hosted structure job artifacts with
  guardrails that require explicit docking limitation acknowledgements.
- Record optional structure-aware filter pass/fail state without claiming that a
  molecule is safe, binds a target, or is practically synthesizable.
- Rank generated structures separately from evidence-backed molecules.
- Write `candidates.json`, `generated_candidates.json`,
  `generation_trace.json`, `developability.json`,
  `developability_assessments.json`, `developability_report.md`, `report.md`,
  and `trace.json`.
- Cache real public API responses with source provenance and TTL.
- Provide adapter health checks and opt-in live smoke tests.
- Import user-supplied assay result CSV/JSON files, validate and normalize
  result records, link results to candidates and exact generated structures,
  summarize experimental evidence, adjust scores only when linked and
  QC-appropriate, and suggest active-learning batches for expert triage.
- Build endpoint-specific learning datasets from imported QC-passed assay
  results without pooling unrelated endpoints unless explicitly configured and
  labeled.
- Train optional local assay surrogate models when enough labeled data exists,
  with deterministic feature names, leakage-aware train/test split manifests,
  calibration metadata, uncertainty scores, and applicability-domain checks.
- Persist model cards, training manifests, metrics, and prediction artifacts as
  model records, not `EvidenceItem` records and not assay results.
- Let oracle scoring and active design use calibrated surrogate prediction
  artifacts only as weak prioritization signals; generated molecules still need
  exact imported experimental results to gain direct evidence.
- Optimize V1.4 research portfolios with deterministic multi-objective scoring,
  budget limits, diversity constraints, correlated-risk penalties,
  sensitivity-analysis scenarios, and program decision memos.
- Separate candidates into balanced advance, expert review, assay triage,
  learning batch, and deprioritization queues without using Codex to compute
  selections or scores.
- Surface overrepresented and underexplored targets, mechanisms, and chemical
  series, plus stage gates for decisions that require human approval.
- Build a provenance-aware `KnowledgeGraph` from existing ranking, literature,
  assay, developability, generated-molecule, review, and portfolio artifacts.
- Represent graph memory with `GraphEntity` and `GraphRelation` records,
  ontology/identifier normalization, source provenance, and explicit hypothesis
  status for graph-inferred relationships.
- Query recurring mechanisms, target outcomes, scaffold and chemical-series
  performance, assay contradictions, repeated developability blockers,
  literature/experimental disagreements, expert-review outcome patterns,
  generated-molecule novelty versus known chemistry, and stale or unsupported
  hypotheses.
- Render a graph dashboard and provide a Codex graph assistant that summarizes
  graph-backed patterns without creating evidence, assay results, citations,
  graph records, mechanisms, or biomedical claims.
- Generate graph-backed hypotheses across mechanism, molecule-target,
  generated-molecule follow-up, developability risk, assay contradiction,
  scaffold/series, evidence-gap, active-learning, portfolio decision, and
  high-level validation-question categories.
- Plan high-level research questions and validation plans that remain
  reviewable planning artifacts, not lab protocols or experimental procedures.
- Attach uncertainty, contradiction checks, evidence gaps, falsification
  criteria, rank scores, lifecycle events, and review status to every generated
  hypothesis.
- Provide a guarded Codex hypothesis assistant whose outputs are withheld when
  they invent evidence, assay results, citations, graph records, hypotheses, or
  unsafe procedural content.
- Register hosted model training, model validation, and model prediction jobs
  with guardrails that reject patient, clinical, and dosing data.
- Register local runs in a `ProjectWorkspace`, track artifacts through an
  `ArtifactRegistry`, compare multiple runs, generate a project dashboard, and
  expose a local JSON project API.
- Use `CodexCLIProvider` to call Codex CLI through subprocess execution with
  working-directory isolation, timeouts, dry-run and disabled modes, structured
  prompts, JSON validation, artifact manifests, guardrails, audit logs, and
  status capture.
- Run Codex-backed assistant commands for project planning, report
  summarization, candidate/run comparison summaries, review questions,
  active-learning explanations, and follow-up computational task planning.
- Run engineering automation commands for lint, typecheck, tests, and
  Codex-backed engineering plans.
- Store Codex-backed outputs in separate `codex_backbone` artifacts so they do
  not become biomedical evidence, assay results, generated molecules, review
  decisions, or score updates.
- Run Codex guardrails and evals for JSON validity, artifact grounding,
  citation fabrication, forbidden biomedical claims, and safe command planning.
- Run hosted mode with bearer-token authentication, user accounts,
  organizations, teams, project-level owner/editor/viewer permissions, audit
  logs, `/dashboard`, `/ops/health`, admin user controls, data export/delete,
  retention policies, and a central SQLite/PostgreSQL platform database.
- Queue hosted Codex work as allowlisted project jobs. Hosted API callers cannot
  submit arbitrary Codex prompts or shell-capable tasks; a `CodexWorker` builds
  server-side tasks from registered project artifacts.
- Configure external integration connectors for ELN/LIMS, compound registry,
  assay providers, Benchling, generic REST, generic CSV/SFTP-style staging,
  PostgreSQL-compatible warehouses, optional Databricks SQL and Snowflake
  connectors, signed webhooks, and a SiLA metadata-only adapter placeholder.
- Ingest webhooks and connector imports with source system, source record ID,
  sync job ID, timestamps, raw metadata, data-contract validation, sync audit
  logs, health checks, and an integration dashboard.
- Export curated molecule-ranker warehouse tables for candidates, generated
  molecules, targets, evidence, literature claims, developability assessments,
  assay results, review decisions, active-learning suggestions, sync jobs, and
  artifact manifests.
- Keep integration credentials as environment/vault references or hashed
  platform secrets; plaintext credentials are not stored or surfaced.
- Let Codex suggest external-ID mappings only as assistant output. Deterministic
  validation against observed source records must confirm mappings before use.

V2.0 does not:

- Create placeholder molecules.
- Use fixture biomedical data in production.
- Use hardcoded generated molecules.
- Invent fallback targets, molecules, evidence, citations, or scores.
- Invent evidence for generated molecules.
- Invent structures, poses, binding sites, docking scores, or interactions.
- Promote docking scores, poses, or structure-based scores to evidence.
- Let graph inference create `EvidenceItem` records or assay results.
- Let graph paths prove causality, efficacy, safety, binding, or activity.
- Treat campaign plans as lab protocols or experimental procedures.
- Provide synthesis routes, reagents, concentrations, incubation times,
  temperatures, animal dosing, human dosing, or patient treatment guidance.
- Let Codex invent campaign metrics, assay results, costs, evidence, citations,
  molecules, mechanisms, outcomes, priorities, budget fit, dependencies, or
  replan triggers.
- Let Codex invent graph nodes, graph edges, citations, results, or mechanisms.
- Treat automated hypotheses as evidence.
- Treat research questions as lab protocols.
- Treat validation plans as experimental procedures.
- Let Codex invent hypotheses without deterministic validation of every
  referenced entity, relation, citation, result, and artifact.
- Use LLMs to invent citations, paper claims, or biomedical relationships.
- Create fake citations or placeholder papers.
- Create synthesis protocols, retrosynthesis, synthesis planning, wet-lab,
  dosage, patient-treatment, or clinical guidance.
- Run docking unless it is explicitly enabled.
- Store full copyrighted articles.
- Claim that a molecule cures, treats, or is active against a disease.
- Claim ADMET predictions prove clinical safety.
- Claim docking scores prove binding.
- Claim synthetic-accessibility heuristics prove practical synthesizability.
- Provide synthesis routes, reagents, reaction conditions, or synthesis
  instructions.
- Make patient-specific recommendations.
- Fabricate assay results, infer assay outcomes from model scores, or treat
  surrogate model predictions as experimental evidence.
- Promote model predictions to `EvidenceItem`, assay results, direct evidence,
  activity claims, safety claims, efficacy claims, binding claims, treatment
  claims, or cure claims.
- Train surrogate models across unrelated assay endpoints unless endpoint
  pooling is explicitly configured and labeled.
- Let Codex invent model metrics or prediction values.
- Let Codex invent evaluation metrics, outcomes, labels, benchmark results,
  assay results, or conclusions.
- Let Codex alter benchmark results, hide guardrail failures, claim clinical
  validation, or create evidence.
- Treat Codex CLI as a biomedical source of truth.
- Let Codex invent targets, molecules, assay results, citations, evidence, or
  scores.
- Let Codex create portfolio selections, optimization outputs, stage gates, or
  decision memos without deterministic validation.
- Let Codex directly alter scores without calling molecule-ranker scoring
  modules.
- Treat portfolio recommendations as clinical, lab, synthesis, dosing, or
  experimental instructions.
- Claim selected portfolio molecules are safe, active, effective, or
  synthesizable.
- Expose cache files, secrets, bearer tokens, API keys, or hidden environment
  files through the hosted API, dashboard, audit logs, artifacts, or Codex
  prompts.
- Run lab instruments, control devices, provide lab protocols, provide synthesis
  instructions, provide dosing, or provide patient-treatment guidance.
- Write to external systems by default. Connector modes default to dry-run,
  read-only, or sandbox; writes/exports require explicit config and permission.
- Add major new science modules beyond stability, validation, security, and
  enterprise readiness hardening.
- Treat enterprise validation packages as clinical validation or biomedical
  evidence.

Unit tests use mocked data only to test behavior deterministically. Production
code uses real public biomedical data adapters and fails if required data cannot
be retrieved.

## Enterprise Quickstart

Install dependencies and verify the version:

```bash
uv sync --all-groups
uv run molecule-ranker version
```

Current release: `2.1.0`.

Confirm the frozen V2.0 contracts and API surface:

```bash
uv run molecule-ranker v2 validate-contracts
uv run molecule-ranker api export-openapi --output openapi.json
```

Run a source-backed ranking workflow locally. Generation, docking, external
writes, Codex, review workflows, and experimental evidence are disabled unless
explicitly configured or enabled.

```bash
uv run molecule-ranker rank "<disease-name>" \
  --top 5 \
  --output-dir results/<disease-slug>
```

Register a project workspace and attach the run artifacts:

```bash
uv run molecule-ranker project create \
  --root ./research/example \
  --workspace-id example-project \
  --name "Example Project"

uv run molecule-ranker project run \
  results/example-disease-a/example-disease-a \
  --root ./research/example \
  --run-id run-001
```

Use the synthetic V1.0 demo for an offline, non-biomedical walkthrough:

```bash
cd examples/v1_0_demo
./demo_commands.sh
```

The demo uses clearly fake names such as `ExampleCandidateA`,
`ExampleTargetA`, and `ExampleDiseaseA`. It does not contain fake real-world
biomedical claims, fake citations, fake assay outcomes, lab protocols, dosing,
or synthesis instructions.

Run the mocked enterprise golden workflow:

```bash
uv run molecule-ranker validate enterprise-golden
```

## V2.1 Codex Runtime Agent OS

V2.1 makes Codex CLI the runtime LLM agent backbone for molecule-ranker.
Codex can understand a research objective, build a safe action plan, and
execute approved molecule-ranker tools, but only through the controlled runtime
stack:

`CodexRuntimeAgent -> ActionPlanner -> RuntimeToolRegistry -> PolicyEngine -> ApprovalGate -> ActionExecutor -> ArtifactValidator -> GuardrailChecker -> AuditLogger`

Codex runtime actions are orchestrated tool calls, not biomedical evidence.
Codex cannot invent scientific truth, citations, assay results, molecules,
scores, mechanisms, or claims. Codex cannot bypass deterministic validators,
RBAC, policy, artifact contracts, or guardrails. Codex cannot approve stage
gates, campaign advancement, external writes, generated-molecule export, or
other human-only approvals. Runtime actions are audited end to end.

Autonomy levels control what Codex can do:

- `observe_only`: inspect artifacts and summarize; no writes.
- `suggest_only`: create plans only; no execution.
- `execute_safe_tools`: execute no-side-effect and artifact-write tools.
- `execute_with_approval`: execute safe tools and request approval for risky
  tools.
- `full_auto_restricted`: execute approved tool classes within policy, while
  still requiring human approval for external writes, generated exports,
  destructive actions, stage gates, and campaign advancement.

Approval queues protect risky actions. Runtime skills provide reusable workflow
templates for common workflows such as rank-and-review, generation-and-triage,
experiment feedback, graph/hypothesis/campaign planning, evaluation/reporting,
integration dry runs, and support diagnostics. Codex permission profiles scope
filesystem and network access for runtime, integration, and engineering modes;
managed profiles never generate `danger-full-access` by default and deny
`.env`, cache, secrets, and credential paths.

Start an agent in `suggest_only` mode:

```bash
uv run molecule-ranker agent start \
  --goal "Rank Alzheimer disease and create a review workspace" \
  --autonomy suggest_only \
  --dry-run \
  --output-dir .molecule-ranker/runtime-agent/alzheimer-review
```

Start an agent in `execute_safe_tools` mode:

```bash
uv run molecule-ranker agent start \
  --goal "Rank Parkinson disease and create a report" \
  --autonomy execute_safe_tools \
  --output-dir .molecule-ranker/runtime-agent/parkinson-ranking
```

Run the rank-and-review skill through deterministic skill expansion:

```bash
uv run molecule-ranker agent start \
  --goal "Use the rank-and-review workflow for Parkinson disease" \
  --autonomy execute_with_approval \
  --output-dir .molecule-ranker/runtime-agent/rank-and-review
```

Approve a runtime action:

```bash
uv run molecule-ranker agent approve \
  --request .molecule-ranker/runtime-agent/runtime_approval_request.json \
  --decided-by reviewer-1 \
  --rationale "Reviewed deterministic plan and approved the requested action."
```

Inspect the runtime audit trail:

```bash
uv run molecule-ranker agent audit \
  --output-dir .molecule-ranker/runtime-agent/rank-and-review
```

Run runtime-agent evals:

```bash
uv run molecule-ranker agent eval --suite runtime
```

Generate a managed Codex permission profile:

```bash
uv run molecule-ranker codex permissions generate \
  --profile read_only_runtime
```

## V2.0 Enterprise Release

V2.0 ships molecule-ranker as a validated enterprise discovery operating
system. The release focuses on stable contracts, production deployment,
security, enterprise identity and access controls, tenant/project isolation,
validation evidence packages, runbooks, disaster recovery, governance, audit
readiness, performance/reliability targets, release certification, stable APIs
and SDK, enterprise admin controls, end-to-end synthetic demos, and training.
No major new science modules are added. It does not introduce major new science
capabilities, and it does not weaken
scientific guardrails. Generated molecules remain computational hypotheses,
model predictions remain prioritization artifacts, benchmark outputs remain
evaluation artifacts, and Codex output remains separate from evidence, assay
results, molecules, scores, benchmark results, or decisions.

V2.0 enterprise validation evidence is software/process validation evidence:
release checks, deterministic golden workflows, contract manifests, security
and guardrail audits, deployment readiness, backup/restore verification,
audit/governance evidence, support bundles, and operator runbooks. It is not
clinical validation and must not be represented as proof that any molecule is
active, safe, effective, synthesizable, or suitable for treatment.

Run the inherited pilot readiness audit:

```bash
uv run molecule-ranker pilot readiness --json
uv run molecule-ranker pilot readiness --output pilot_readiness_report.md
```

Generate a redacted support bundle:

```bash
uv run molecule-ranker support bundle \
  --output support_bundle.zip
```

Run a synthetic baseline performance profile:

```bash
uv run molecule-ranker performance profile \
  --workflow golden \
  --output-dir results/performance
```

Run an artifact migration dry-run with backup-aware manifest generation:

```bash
uv run molecule-ranker migrate artifacts \
  --path results/ \
  --target-version 2.0 \
  --dry-run
```

Run the V2.0 release certification checks:

```bash
uv run molecule-ranker release check --json
uv run molecule-ranker release manifest --output release_manifest.json
uv run molecule-ranker release notes --output RELEASE_NOTES.md
uv run molecule-ranker v2 release-gate --output-dir release_gate/
```

Start the synthetic enterprise demo environment:

```bash
cd examples/v2_0_enterprise_demo
./scripts/bootstrap.sh
./scripts/run_demo_workflows.sh
./scripts/generate_support_bundle.sh
```

Submit pilot feedback. Feedback is operational input, not scientific evidence:

```bash
uv run molecule-ranker feedback submit \
  --project-id synthetic-demo-project \
  --page-or-command dashboard \
  --type usability_issue \
  --severity medium \
  "The failed-job remediation panel needs a clearer next action."
```

Run operational alert checks:

```bash
uv run molecule-ranker ops alerts
```

Retry a failed job from the admin support API. This requires an admin token and
writes an audit event:

```bash
curl -X POST "$MOLECULE_RANKER_HOST/admin/support/jobs/$FAILED_JOB_ID/retry" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN"
```

## V1.1 Agentic Design Optimization

V1.1 upgrades molecule-ranker from opt-in molecule generation to an auditable
agentic design optimization workflow. The goal is better computational
prioritization for expert triage, not proof that generated structures work.
V1.1 improves generated molecule quality by combining source-grounded design
plans, seed/scaffold selection, generator ensembles, oracle scoring,
uncertainty estimation, medicinal chemistry critique, active-design feedback,
and experiment-readiness ranking.

Experiment-readiness means a generated molecule may be worth expert review and
possible assay triage. It does not mean proven activity, target engagement,
safety, efficacy, or practical synthesizability. Generated molecules remain
computational hypotheses and are kept separate from evidence-backed existing
molecules by default.

Codex can plan, critique, summarize, and orchestrate bounded workflows, but it
cannot create scientific truth. Codex-generated design plans must pass
deterministic validation before execution, may only reference imported
artifacts, and require review/approval before large hosted generation jobs.
Experimental feedback can guide active design only through imported results
linked to exact tested structures. Surrogate models and oracle scores are
prioritization signals, not evidence.

V1.1 design workflows do not provide synthesis instructions, synthesis routes,
reagents, reaction conditions, lab protocols, dosing, animal-study instructions,
patient guidance, or medical advice.

Create a deterministic design plan from existing run artifacts:

```bash
uv run molecule-ranker design plan \
  --run-dir results/parkinson-disease \
  --disable-codex-planner \
  --strict-guardrails
```

Run a complete local design loop. The loop writes `design_plan.json`,
`generated_candidates_v2.json`, `oracle_scores.json`,
`experiment_readiness.json`, `benchmark_report.json`, `benchmark_report.md`,
and `design_loop_report.md`.

```bash
uv run molecule-ranker design loop \
  --run-dir results/parkinson-disease \
  --generator selfies_mutation \
  --generator fragment_grower \
  --generator scaffold_hopper \
  --generator matched_pair \
  --budget 64 \
  --max-retained 20 \
  --random-seed 13 \
  --strict-guardrails
```

Benchmark generated hypotheses with internal V1.1 metrics:

```bash
uv run molecule-ranker design benchmark \
  --input results/parkinson-disease/generated_candidates_v2.json \
  --output-dir results/parkinson-disease \
  --random-seed 13
```

Inspect experiment-readiness candidates for expert triage:

```bash
jq '.candidates[] | {
  molecule_id,
  readiness_bucket,
  readiness_score,
  top_reasons,
  blocking_risks
}' results/parkinson-disease/experiment_readiness.json
```

Run a hosted V1.1 design job. Hosted design jobs require `design:run`;
Codex-planned or large generation jobs require plan approval and budget limits.
Generated molecules are labeled computational hypotheses, and export requires
`design:export` plus warning acknowledgement.

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/design/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "design_loop",
    "run_id": "run-001",
    "budget": 64,
    "budget_limit": 100,
    "generator": ["selfies_mutation", "fragment_grower"],
    "use_codex_planner": false,
    "plan_approved": true,
    "warning_acknowledged": true
  }'
```

Approve a Codex-generated design plan after source-backed review:

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/design/plans/approve" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "plan_id": "design-plan-001",
    "run_id": "run-001",
    "approval_note": "Reviewed against source-backed run artifacts."
  }'
```

## V1.2 Predictive Model Plugins and Surrogates

V1.2 adds a stronger predictive model plugin system and calibrated
assay-specific surrogate models. Models learn only from imported assay results
that pass QC and link to exact candidate identities. They do not learn from
literature claims, Codex summaries, seed analog assumptions, patient data,
clinical data, dosing data, or generated-molecule hypotheses without exact
imported result linkage.

Model predictions are separate from evidence and experimental results. A
prediction artifact is never an `EvidenceItem`, never an assay result, and
never a claim of activity, binding, safety, efficacy, treatment, or cure.
Predictions are endpoint-specific and context-specific; unrelated endpoints are
kept separate unless explicit pooling is configured and labeled.

Calibration and applicability-domain checks are required for trustworthy
interpretation. Uncalibrated predictions, insufficient-calibration predictions,
unknown-domain predictions, and out-of-domain predictions are flagged and are
ignored or penalized by guarded scoring paths. Generated molecule predictions
are hypothesis-prioritization signals only; generated molecules still require
exact imported experimental results for the tested structure to gain direct
evidence.

V1.2 model workflows do not provide medical advice, synthesis instructions, lab
protocols, dosing, patient guidance, or clinical claims.

Build an endpoint-specific model dataset from imported assay results:

```bash
uv run molecule-ranker model dataset build \
  --db-path .experiments/results.sqlite \
  --endpoint-name synthetic_maob_activity \
  --target-symbol MAOB \
  --disease-name "Parkinson disease" \
  --label-type binary \
  --output-dir results/parkinson-disease/models/dataset \
  --feature-family rdkit_descriptors \
  --feature-family target_context
```

Train a baseline local surrogate:

```bash
uv run molecule-ranker model train \
  --dataset results/parkinson-disease/models/dataset/<dataset-id>_manifest.json \
  --model-type random_forest \
  --split-strategy scaffold \
  --output-dir results/parkinson-disease/models/training \
  --random-seed 17
```

For small synthetic or dependency-light validation runs, use the pure-Python
dummy baseline:

```bash
uv run molecule-ranker model train \
  --dataset results/parkinson-disease/models/dataset/<dataset-id>_manifest.json \
  --model-type dummy \
  --split-strategy random \
  --output-dir results/parkinson-disease/models/training
```

Evaluate a model with leakage checks:

```bash
uv run molecule-ranker model evaluate \
  --model-card results/parkinson-disease/models/training/model_card.json \
  --dataset results/parkinson-disease/models/dataset/<dataset-id>_manifest.json \
  --output-dir results/parkinson-disease/models/evaluation
```

Calibrate a model against held-out labels:

```bash
uv run molecule-ranker model calibrate \
  --model-card results/parkinson-disease/models/training/model_card.json \
  --dataset results/parkinson-disease/models/dataset/<dataset-id>_manifest.json \
  --output-dir results/parkinson-disease/models/calibration
```

Predict on saved run artifacts without creating evidence:

```bash
uv run molecule-ranker model predict \
  --model-card results/parkinson-disease/models/training/model_card.json \
  --from-run results/parkinson-disease/ \
  --output results/parkinson-disease/model_predictions.json
```

Use predictions in the design oracle only as bounded prioritization signals.
Attach prediction artifacts to generated candidate metadata and enable the
surrogate oracle in the design/oracle config:

```json
{
  "enable_predictive_models": true,
  "enable_surrogate_oracle": true,
  "surrogate_oracle_endpoint_id": "endpoint-model-validation-maob",
  "surrogate_oracle_weight": 0.08,
  "require_calibrated_predictions": true,
  "allow_uncalibrated_with_warning": false,
  "min_prediction_confidence": 0.5,
  "out_of_domain_penalty": 0.08
}
```

Inspect the local model registry:

```bash
uv run molecule-ranker model registry list
uv run molecule-ranker model registry show model-validation-baseline
uv run molecule-ranker model registry export model-validation-baseline
```

Run deterministic V1.2 model validation:

```bash
uv run molecule-ranker validate models
```

The model validation workflow imports synthetic assay results, builds an
endpoint-specific dataset, trains a baseline surrogate, evaluates leakage,
calibrates when enough data exist, predicts on existing/generated candidates,
integrates eligible predictions into oracle scoring, generates model reports,
and verifies guardrails.

## V1.5 Cross-Program Knowledge Graph

V1.5 adds a cross-program knowledge graph and mechanism-level reasoning layer
over existing molecule-ranker artifacts. The graph integrates diseases,
targets, pathways, mechanisms, molecules, generated hypotheses, scaffolds,
chemical series, assays, assay results, literature papers and claims, evidence
items, developability risks, structures, docking poses, model predictions,
expert reviews, portfolios, projects, programs, and portfolio context.

Graph relations preserve provenance through source artifact IDs, source record
IDs, evidence item IDs, timestamps, confidence values, relation type, and
direction. Inferred graph relations are explicitly labeled hypotheses, not
evidence. They must not become `EvidenceItem` records and must not create assay
results. Mechanism hypotheses summarize graph-linked disease, target, pathway,
and molecule evidence for review, but they are not proof of causality,
efficacy, safety, binding, or activity.

Contradictions and stale decisions are surfaced rather than hidden. Positive
and negative assay outcomes, literature disagreements, high model predictions
contradicted by experiments, stale reviews, stale portfolios, and repeated
developability blockers are retained with provenance so experts can inspect the
conflict. Codex can explain graph paths and draft graph review questions from
existing graph facts, but it cannot create graph nodes, graph edges, citations,
mechanisms, assay results, evidence, or confidence scores.

RDF/Turtle export is available for interoperability. Graph exports do not
include secrets, cache payloads, full copyrighted articles, or generated-
molecule overclaims. V1.5 graph workflows provide no medical advice, synthesis
instructions, lab protocols, dosing, patient guidance, or clinical claims.

Build a graph from a registered project workspace:

```bash
uv run molecule-ranker graph build \
  --from-project ./research/example \
  --output graph.json
```

Query candidates for a target:

```bash
uv run molecule-ranker graph query \
  --graph graph.json \
  --query candidates_for_target \
  --target-symbol LRRK2 \
  --json
```

Extract mechanism hypotheses for a disease:

```bash
uv run molecule-ranker graph mechanism \
  --graph graph.json \
  --disease "Parkinson disease" \
  --output mechanisms.json
```

Detect contradictions:

```bash
uv run molecule-ranker graph contradictions \
  --graph graph.json \
  --output contradiction_report.json
```

Detect stale decisions and stale graph-linked records:

```bash
uv run molecule-ranker graph stale \
  --graph graph.json \
  --output staleness_report.json
```

Export the graph as RDF/Turtle:

```bash
uv run molecule-ranker graph export \
  --graph graph.json \
  --format ttl \
  --output graph_export.ttl
```

Generate a static graph dashboard:

```bash
uv run molecule-ranker graph dashboard \
  --graph graph.json \
  --output graph_dashboard/
```

Queue a hosted graph job. Hosted graph jobs require project access and graph
permissions such as `graph:build`, `graph:query`, or `graph:export`.
Cross-program graph jobs require permission across every included project.
Graph recommendations are advisory and are not automatic decisions.

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/graph/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "graph_query",
    "query": "candidates_for_target",
    "target_symbol": "LRRK2",
    "graph_artifact_id": "artifact-knowledge-graph"
  }'
```

Run deterministic V1.5 graph validation:

```bash
uv run molecule-ranker validate graph
```

The graph validation workflow builds synthetic artifacts for two projects,
builds and deduplicates the graph, extracts mechanisms, detects contradictions
and stale decisions, generates recommendations, exports RDF/Turtle, writes a
dashboard, and verifies graph guardrails.

## V1.6 Hypothesis Generation and Research-Question Planning

V1.6 adds automated hypothesis generation and testable research-question
planning. Hypotheses are generated from the provenance-aware knowledge graph,
mechanism hypotheses, contradictions, evidence gaps, portfolio outputs, review
decisions, and experimental results. The hypothesis engine produces reviewable
planning records for mechanistic, molecule-target, generated-molecule
follow-up, developability-risk, assay-contradiction, cross-program
scaffold/series, evidence-gap, active-learning, portfolio-decision, and
high-level validation-question use cases.

Every generated hypothesis keeps cited entity IDs, relation IDs, provenance IDs,
artifact IDs, uncertainty text, contradiction links, evidence gaps,
falsification criteria, rank scores, lifecycle events, and review status. A
hypothesis is not evidence. A research question is not a lab protocol. A
validation plan is not an experimental procedure. Falsification criteria are
high-level decision criteria, not procedures. Generated-molecule hypotheses
require human review before follow-up planning.

Codex can draft hypothesis language and research questions only from supplied
graph-backed context. Deterministic validation must confirm every referenced
entity, relation, citation, result, and artifact before output is accepted.
Codex can draft wording but cannot invent facts: it cannot invent evidence,
assay results, citations, graph records, mechanisms, or hypotheses, and it
cannot provide synthesis routes, reagents, concentrations, temperatures,
incubation times, dosing, step-by-step experimental instructions, medical
advice, patient treatment guidance, or clinical claims.

Generate hypotheses from a graph:

```bash
uv run molecule-ranker hypothesis generate \
  --from-graph graph.json \
  --max-hypotheses 25 \
  --output hypotheses.json
```

Generate high-level research questions:

```bash
uv run molecule-ranker hypothesis questions \
  --hypotheses hypotheses.json \
  --output research_questions.json
```

List evidence gaps for hypothesis planning:

```bash
uv run molecule-ranker hypothesis gaps \
  --hypotheses hypotheses.json \
  --output evidence_gaps.json
```

Create falsification criteria:

```bash
uv run molecule-ranker hypothesis falsification \
  --hypotheses hypotheses.json \
  --output falsification_criteria.json
```

Rank hypotheses for research planning:

```bash
uv run molecule-ranker hypothesis rank \
  --hypotheses hypotheses.json \
  --output ranked_hypotheses.json
```

Record a human review decision:

```bash
uv run molecule-ranker hypothesis review \
  --hypothesis-id hypothesis:example \
  --decision needs_more_evidence \
  --reviewer-id reviewer-123 \
  --rationale "Needs additional source-backed context before planning."
```

Generate a hypothesis report:

```bash
uv run molecule-ranker hypothesis report \
  --hypotheses ranked_hypotheses.json \
  --output hypothesis_report.md
```

Queue a hosted hypothesis job. Hosted hypothesis jobs require hypothesis
permissions, keep hypotheses separate from evidence, validate Codex-drafted
wording deterministically, and require human review for generated-molecule
hypotheses before follow-up planning.

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/hypothesis/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "hypothesis_generate",
    "graph_artifact_id": "artifact-knowledge-graph",
    "max_hypotheses": 25,
    "use_codex_drafting": false,
    "require_human_review_for_generated_hypotheses": true,
    "strict_hypothesis_guardrails": true
  }'
```

Run deterministic V1.6 hypothesis validation:

```bash
uv run molecule-ranker validate hypotheses
```

## V1.7 Closed-Loop Campaign Planning

V1.7 adds deterministic closed-loop campaign planning and budget-aware execution
management. Campaigns convert hypotheses, portfolios, active-learning
suggestions, review decisions, experimental evidence, and graph contradictions
into high-level work packages for research teams. Campaign plans can include
priority scores, review-gated work packages, assay/review/computation slot
allocations, dependencies, decision gates, expected learning value, opportunity
cost, replan triggers, audit trails, campaign memos, and campaign dashboards.

Campaign plans are research-management artifacts, not lab protocols. They do
not include procedural experimental instructions, synthesis instructions,
reagents, concentrations, incubation times, temperatures, animal dosing, human
dosing, patient guidance, medical advice, or clinical claims. Budget and
resource estimates are planning estimates only; no real vendor, lab, or program
cost is inferred unless it is imported from configured external data. Generated
molecules remain computational hypotheses unless exact imported experimental
evidence exists.

Replanning is triggered deterministically by new results, review decisions,
graph contradictions, model retraining, external sync updates, budget changes,
failed work packages, and hypothesis status changes. Stage gates enforce human
approval for configured campaign advancement, generated-molecule review,
budget exceptions, safety review, contradiction resolution, replanning, and
stop/continue decisions. Codex can draft memos, explain tradeoffs, summarize
budget bottlenecks, draft review questions, and explain replan triggers from
deterministic campaign artifacts. Codex cannot approve campaigns or gates,
invent costs, invent evidence, invent assay results, create campaign plans
without deterministic validation, or turn work packages into protocols.

Create a draft campaign from hypothesis and portfolio artifacts:

```bash
uv run molecule-ranker campaign create \
  --project-id project-1 \
  --program-id program-1 \
  --name "LRRK2 evidence-gap campaign" \
  --description "High-level campaign planning for reviewed hypotheses." \
  --from-hypotheses ranked_hypotheses.json \
  --from-portfolio portfolio_optimization.json \
  --output campaign.json
```

Plan the campaign under assay, review, and compute budgets:

```bash
uv run molecule-ranker campaign plan \
  --campaign campaign.json \
  --budget-assay-slots 4 \
  --budget-review-hours 12 \
  --budget-compute-units 20 \
  --strategy balanced \
  --output campaign_plan.json
```

Approve a stage gate as a human reviewer:

```bash
uv run molecule-ranker campaign approve \
  --campaign-id campaign:example \
  --stage-gate-id campaign-gate:example \
  --reviewer-id reviewer-123 \
  --rationale "Reviewed deterministic artifacts and approved this planning gate."
```

Update a work package and preserve the audit trail:

```bash
uv run molecule-ranker campaign update-work-package \
  --work-package-id wp-generated-review-v17 \
  --status completed \
  --actor reviewer-123
```

Replan after importing a new result or review event artifact:

```bash
uv run molecule-ranker campaign replan \
  --campaign-id campaign:example \
  --event-artifact imported_result_event.json \
  --output updated_campaign_plan.json
```

Generate a campaign memo from a deterministic campaign plan. `--use-codex`
keeps the memo as assistant output and does not let Codex approve, price, or
compute the plan.

```bash
uv run molecule-ranker campaign memo \
  --campaign-plan campaign_plan.json \
  --output campaign_memo.md

uv run molecule-ranker campaign memo \
  --campaign-plan campaign_plan.json \
  --use-codex \
  --output campaign_memo.md
```

Queue a hosted campaign job. Campaign jobs require campaign permissions,
generated-molecule follow-up requires a review gate, and Codex memos are stored
separately from deterministic campaign plans.

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/campaign/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "campaign_plan",
    "campaign_id": "campaign:example",
    "strategy": "balanced",
    "generated_molecule_followup": true,
    "generated_review_gate_present": true,
    "config": {
      "budget_assay_slots": 4,
      "budget_review_hours": 12,
      "budget_compute_units": 20
    }
  }'
```

## V1.8 Scientific Evaluation Benchmarks and Prospective Analytics

V1.8 adds a scientific evaluation benchmark suite and prospective validation
analytics for measuring molecule-ranker platform behavior over time. Evaluation
artifacts measure platform performance; they are not biomedical evidence, not
clinical validation, and not proof that molecules are active, safe, effective,
or synthesizable.

Benchmark labels come only from imported assay results or explicit synthetic
fixtures. Model predictions, docking scores, structure scores, portfolio
decisions, Codex summaries, and graph inferences are not outcome labels.
Generated molecules require exact linked imported results for hit metrics; seed
molecule results do not count as generated analog success.

Prospective validation freezes candidate rankings, predictions, portfolio
selections, or campaign decisions before outcomes are imported. Outcomes
imported before a prediction freeze do not count as prospective, modified
prediction artifacts invalidate the prospective run, and failed-QC outcomes are
not treated as positive or negative evidence.

Guardrail benchmarks test medical advice, synthesis instructions, lab
protocols, dosing or patient guidance, fake citations, fake assay results, fake
molecule evidence, generated-molecule overclaims, docking overclaims, model
prediction overclaims, graph-causality overclaims, unsupported Codex claims,
unsafe external-integration writes, and secret leakage.

Reproducibility manifests track code version, artifact contract version, config
hashes, input and output artifact hashes, random seeds, dependency summaries,
model artifact hashes, Codex transcript presence when used, and external
integration payload hashes.

Codex can summarize evaluation reports, explain metric changes, draft benchmark
limitations, summarize prospective validation analytics, explain guardrail
failures, and draft decision-quality lessons. Codex cannot invent metrics,
outcomes, labels, assay results, benchmark results, or conclusions; every
evaluation Codex output must cite the evaluation ID, task ID, dataset ID, split
ID, metric IDs, and artifact IDs it used.

Create a benchmark suite:

```bash
uv run molecule-ranker eval suite create \
  --name "Example V1.8 benchmark suite" \
  --task candidate_ranking \
  --task surrogate_prediction \
  --output results/<disease-slug>/benchmark_suite.json
```

Build a benchmark dataset from a completed run. The builder preserves source
artifact IDs and row provenance, excludes failed-QC labels unless the task is
explicitly evaluating QC handling, and does not use model predictions as
outcome labels.

```bash
uv run molecule-ranker eval dataset build \
  --from-run results/<disease-slug>/ \
  --task-type candidate_ranking \
  --output results/<disease-slug>/benchmark_dataset.json
```

Create a split:

```bash
uv run molecule-ranker eval split \
  --dataset results/<disease-slug>/benchmark_dataset.json \
  --split-type scaffold \
  --output results/<disease-slug>/benchmark_split.json
```

Run an evaluation with baseline comparison:

```bash
uv run molecule-ranker eval run \
  --suite results/<disease-slug>/benchmark_suite.json \
  --dataset results/<disease-slug>/benchmark_dataset.json \
  --split results/<disease-slug>/benchmark_split.json \
  --output results/<disease-slug>/evaluation_report.json
```

Freeze prospective predictions before future outcomes are imported:

```bash
uv run molecule-ranker eval prospective freeze \
  --predictions results/<disease-slug>/model_predictions.json \
  --output-dir results/<disease-slug>/prospective_run \
  --task-id surrogate_prediction \
  --model-or-pipeline-version 1.8.0
```

Import outcomes after the freeze:

```bash
uv run molecule-ranker eval prospective import-outcomes \
  --run-dir results/<disease-slug>/prospective_run \
  --outcomes results/<disease-slug>/imported_assay_results.json
```

Evaluate the prospective run:

```bash
uv run molecule-ranker eval prospective evaluate \
  --run-dir results/<disease-slug>/prospective_run
```

Run the guardrail benchmark:

```bash
uv run molecule-ranker eval guardrails \
  --from-run results/<disease-slug>/ \
  --output results/<disease-slug>/guardrail_benchmark_report.json
```

Run a reproducibility check:

```bash
uv run molecule-ranker eval reproducibility \
  --from-run results/<disease-slug>/
```

Queue a hosted evaluation job. Hosted evaluation jobs require
`evaluation:run`, keep evaluation reports separate from evidence, and preserve
the boundary that prospective validation analytics are not clinical validation.

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/evaluation/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "eval_benchmark_run",
    "suite_id": "suite-example-v18",
    "dataset_id": "dataset-example-v18",
    "split_id": "split-example-v18",
    "config": {
      "acknowledge_not_evidence": true
    }
  }'
```

## V1.4 Portfolio Optimization and Program Decision Analytics

V1.4 adds a deterministic portfolio layer for program-level research
prioritization, multi-objective portfolio optimization, and program-level
decision analytics. The platform can build candidate portfolios from existing
molecules, generated hypotheses, developability assessments, imported
experimental results, predictive model artifacts, structure-aware assessments,
and expert review decisions.

Portfolio analytics can help compare:

- balanced candidate portfolios under target, mechanism, chemical-series,
  budget, and risk constraints
- generated hypotheses worth expert review versus assay triage
- overrepresented and underexplored targets, mechanisms, and chemical series
- learning-value candidates under limited next-batch budgets
- correlated-risk clusters that should reduce priority
- scenario robustness under learning-heavy, risk-averse, and budget-expanded
  assumptions
- decisions requiring human approval before action

Portfolio selection is advisory and requires human review before program action.
Generated molecules remain computational hypotheses unless exact imported
experimental evidence exists for the same structure. Portfolio recommendations
do not prove activity, safety, efficacy, binding, or synthesizability.

Codex can explain portfolio tradeoffs, draft guarded decision memos, explain
candidate rejection, compare scenarios, and draft review questions from
deterministic artifacts. Codex cannot select portfolios, invent candidate
metrics, create optimization outputs, approve stage gates, or make final
program decisions without deterministic optimizer outputs and human approval.

V1.4 portfolio workflows provide no lab protocols, synthesis instructions,
dosing, patient treatment guidance, or medical advice.

Build portfolio candidates from a completed run:

```bash
uv run molecule-ranker portfolio build-candidates \
  --from-run results/<disease-slug>/ \
  --output results/<disease-slug>/portfolio_candidates.json
```

Run deterministic portfolio optimization:

```bash
uv run molecule-ranker portfolio optimize \
  --candidates results/<disease-slug>/portfolio_candidates.json \
  --algorithm greedy \
  --max-candidates 8 \
  --max-generated-fraction 0.4 \
  --output results/<disease-slug>/portfolio_optimization.json
```

Run scenario analysis:

```bash
uv run molecule-ranker portfolio scenarios \
  --candidates results/<disease-slug>/portfolio_candidates.json \
  --scenario conservative \
  --scenario exploration \
  --scenario safety_first \
  --output results/<disease-slug>/scenario_analysis.json
```

Build an expert review batch from the selected portfolio:

```bash
uv run molecule-ranker portfolio batch \
  --optimization results/<disease-slug>/portfolio_optimization.json \
  --batch-type expert_review_batch \
  --output results/<disease-slug>/portfolio_batch.json
```

Run a stage gate for a candidate. Generated molecules cannot advance to assay
candidate status by default without explicit review approval:

```bash
uv run molecule-ranker portfolio stage-gate \
  --candidate-id portfolio-candidate-001 \
  --from-run results/<disease-slug>/ \
  --to-stage assay_candidate \
  --reviewer-id reviewer-123 \
  --output results/<disease-slug>/stage_gate_decision.json
```

Draft a guarded decision memo from deterministic optimization output:

```bash
uv run molecule-ranker portfolio memo \
  --optimization results/<disease-slug>/portfolio_optimization.json \
  --output results/<disease-slug>/program_decision_memo.md
```

Queue a hosted portfolio job. Hosted portfolio jobs require portfolio
permissions, keep outputs advisory until approved, and require explicit export
permission before selected portfolios can be written to external systems:

```bash
curl -X POST "$MOLECULE_RANKER_HOST/projects/project-1/portfolio/jobs" \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "portfolio_optimize",
    "run_id": "run-001",
    "config": {
      "algorithm": "greedy",
      "max_candidates": 8,
      "max_generated_fraction": 0.4,
      "require_review_for_generated": true,
      "exclude_critical_risk": true
    },
    "scenarios": ["conservative", "exploration", "safety_first"],
    "use_codex": false
  }'
```

## V1.3 Structure-Based Design and Protein-Ligand Workflow Hardening

V1.3 adds advanced structure-based design and protein-ligand workflow
hardening. Structure workflows are optional and conservative: the normal
source-backed ranking, generation, model, review, and experimental feedback
paths do not require structures or docking.

RCSB PDB and AlphaFold DB can provide target structure metadata. Experimental
structures are preferred when suitable. Predicted structures are lower
confidence than suitable experimental structures and are labeled accordingly in
structure records, selections, reports, hosted jobs, and validation artifacts.

Receptor preparation, ligand 3D preparation, binding-site definition, docking,
pose QC, protein-ligand interaction profiling, consensus rescoring, and
structure-aware assessments are computational workflows. Docking scores do not
prove binding. Poses do not prove activity. Structure-aware assessments are
prioritization signals only; they are not experimental evidence, activity
evidence, safety evidence, or validation. Generated molecules remain
computational hypotheses.

V1.3 structure workflows provide no medical advice, synthesis instructions, lab
protocols, dosing, patient guidance, or clinical claims.
Codex may plan and summarize structure workflows only from cited artifacts; it
may not invent structures, binding sites, residues, poses, docking scores, or
interactions.

Find structure metadata from RCSB PDB:

```bash
uv run molecule-ranker structure find \
  --target-symbol LRRK2 \
  --target-id uniprot:Q5S007 \
  --source rcsb \
  --output results/lrrk2/structures.json
```

Find predicted structure metadata from AlphaFold DB:

```bash
uv run molecule-ranker structure find \
  --target-symbol LRRK2 \
  --target-id Q5S007 \
  --source alphafold \
  --output results/lrrk2/alphafold_structures.json
```

Select a structure with the conservative V1.3 policy:

```bash
uv run molecule-ranker structure select \
  --structures results/lrrk2/structures.json \
  --target-symbol LRRK2 \
  --output results/lrrk2/structure_selection.json
```

Prepare a receptor artifact record. `metadata_only` is the safest default and
does not modify coordinates:

```bash
uv run molecule-ranker structure prepare-receptor \
  --structure-id RCSB_PDB:6XYZ \
  --structure-file artifacts/structures/6xyz.pdb \
  --method metadata_only \
  --output results/lrrk2/receptor_preparation.json
```

Prepare ligand 3D artifacts from an existing run, preserving generated
molecule provenance when included:

```bash
uv run molecule-ranker structure prepare-ligands \
  --from-run results/lrrk2/ \
  --include-generated \
  --max-ligands 25 \
  --output results/lrrk2/ligand_preparation.json
```

Define a binding site from the selected structure metadata. Binding-site
definitions must be provenance-backed; molecule-ranker does not invent residue
lists or boxes:

```bash
uv run molecule-ranker structure define-site \
  --structure-selection results/lrrk2/structure_selection.json \
  --method co_crystal_ligand \
  --output results/lrrk2/binding_sites.json
```

Run the null docking engine for a no-op validation/dry-run path. This writes a
docking run record without claiming docking was performed:

```bash
uv run molecule-ranker structure dock \
  --receptor results/lrrk2/receptor_preparation.json \
  --ligands results/lrrk2/ligand_preparation.json \
  --binding-site results/lrrk2/binding_sites.json \
  --engine null \
  --max-ligands 25 \
  --output results/lrrk2/docking_runs.json
```

Run AutoDock Vina only when it is explicitly enabled, installed, and reviewed.
Vina docking remains computational prioritization only:

```bash
uv run molecule-ranker structure dock \
  --receptor results/lrrk2/receptor_preparation.json \
  --ligands results/lrrk2/ligand_preparation.json \
  --binding-site results/lrrk2/binding_sites.json \
  --engine vina \
  --enable-docking \
  --max-ligands 10 \
  --output results/lrrk2/docking_runs.json
```

Assess pose artifacts and write conservative structure-aware assessments:

```bash
uv run molecule-ranker structure assess \
  --docking-runs results/lrrk2/docking_runs.json \
  --poses results/lrrk2/docking_poses.json \
  --output results/lrrk2/structure_aware_assessments.json
```

Run the structure-aware design loop from Python when generated candidates and
structure-aware assessments are already present. The loop uses
"structure-aware prioritization," not binding optimization:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

from molecule_ranker.generation.schemas import GeneratedMolecule
from molecule_ranker.structure.schemas import StructureAwareAssessment
from molecule_ranker.structure.structure_aware_design import StructureAwareGenerationLoop

run_dir = Path("results/lrrk2")
generated_payload = json.loads((run_dir / "generated_candidates_v2.json").read_text())
assessment_payload = json.loads((run_dir / "structure_aware_assessments.json").read_text())

generated = [
    GeneratedMolecule.model_validate(item)
    for item in generated_payload["retained_generated_molecules"]
]
assessments = [
    StructureAwareAssessment.model_validate(item)
    for item in assessment_payload["structure_aware_assessments"]
]

result = StructureAwareGenerationLoop().plan_next_round(
    generated_candidates=generated,
    assessments=assessments,
    batch_size=8,
)
(run_dir / "structure_aware_design_loop_report.md").write_text(result.report_markdown)
PY
```

Queue a hosted structure job. Docking jobs require `structure:dock`, explicit
warning acknowledgement, and a budget limit for large jobs:

```bash
curl -X POST http://127.0.0.1:8765/projects/<project-id>/structure/jobs \
  -H "Authorization: Bearer $MOLECULE_RANKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "structure_dock",
    "target_symbol": "LRRK2",
    "enable_docking": true,
    "warning_acknowledged": true,
    "max_ligands": 25,
    "budget_limit": 25
  }'
```

Run deterministic V1.3 structure validation:

```bash
uv run molecule-ranker validate structure
```

The structure validation workflow uses mocked structure artifacts to find
structures, select a structure, prepare receptor and ligand records, define a
binding site, run null docking, run pose QC, profile interactions, rescore
consensus, generate a structure report, and verify guardrails. It fails if a
fixture overclaims binding or validation, invents a docking score, or uses a
binding-site source without acceptable provenance.

## V2.0 Release Readiness

V2.0 release readiness is tracked as machine-readable gates in
`molecule_ranker.release`. The release manifest declares the validated
enterprise discovery operating-system scope, scientific-integrity constraints,
non-goals, and stable versioned contracts:

- API contract: `api.v1`.
- Artifact contract: `artifacts.v1`.
- Integration data-contract family: `data-contracts.v1`.
- Warehouse schema contract: `mr_warehouse_v1.0.0`.

The V2.0 gate set covers enterprise release contracts, production deployment,
security hardening, identity and access controls, tenant/project isolation,
validation evidence packages, operational runbooks, disaster recovery and
backup verification, governance and audit readiness, performance and
reliability targets, release certification workflows, stable APIs and SDK,
enterprise admin controls, synthetic end-to-end demo workflows, and V2.0
documentation/training material. See `docs/v2.0-enterprise-release.md`,
`docs/contracts/v2.0-enterprise-contracts.md`, and the runbooks under
`docs/runbooks/`.

### Golden Workflow Validation

Run deterministic golden workflow validation without live external services:

```bash
uv run molecule-ranker validate golden --workflow all
```

Golden workflows use mocked external services and synthetic non-biomedical
test data by default. Live validation is separate and opt-in.

### Release Validation

Run the deterministic release validation suite:

```bash
uv run molecule-ranker validate release
```

Run the release packaging checks and generate release artifacts:

```bash
uv run molecule-ranker release check
uv run molecule-ranker release manifest --output release_manifest.json
uv run molecule-ranker release notes --output RELEASE_NOTES.md
```

Release validation checks artifact contracts, API contracts, golden workflow
outputs, security guardrails, hosted deployment readiness, docs/runbooks, demo
artifacts, backup/restore evidence, and packaging metadata. Default validation
does not call live public APIs, real Benchling or warehouse systems, or live
Codex.

### Validation Package Quickstart

Generate the V2.0 software/platform validation package:

```bash
uv run molecule-ranker validate v2-package --output validation_package/
uv run molecule-ranker validate v2-package --zip validation_package.zip
```

The package contains release manifests, contract validation, mocked golden
workflow evidence, guardrail/security reports, performance and readiness
summaries, backup/restore verification, support-bundle validation, deployment
smoke evidence, Codex guardrail evaluation, integration dry-run validation,
prospective-validation demo evidence, and known limitations. It excludes
secrets, caches, and full copyrighted source text. It is software/process
validation evidence, not clinical validation, regulatory approval, or GxP
compliance unless separately assessed.

### Release Gate Quickstart

Run the V2.0 release gate and write machine-readable and reviewer-readable
outputs:

```bash
uv run molecule-ranker v2 release-gate --output-dir release_gate/
```

The command writes `release_gate/v2_release_gate.json` and
`release_gate/v2_release_gate.md`. CI systems may provide explicit evidence
markers for long-running checks through `--evidence-dir`.

## Deployment Quickstart

Create the platform database, create an admin user, and start the hosted
dashboard locally:

```bash
uv run molecule-ranker db init --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker user create \
  --email admin@example.com \
  --password 'Strong-password-1' \
  --display-name "Platform Admin" \
  --admin \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Then open:

```bash
open http://127.0.0.1:8765/dashboard
```

Production deployments must set a strong secret key, configure allowed hosts,
disable debug mode, enable authentication, configure audit logging, define
retention policies, and configure backup storage. Codex is enabled only when
configured; Codex worker jobs are scoped to registered project artifacts and
require `codex:run` permission. Integrations default to dry-run/read-only and
external writes require explicit write-enabled configuration and permission.

For enterprise packaging, start from `deployment/README.md`,
`deployment/docker-compose.enterprise.yml`, `deployment/helm/`, and
`deployment/hardening.md`. Production deployments should use non-root
containers, health/readiness probes, separate server and worker processes,
PostgreSQL, object/artifact storage, secret-manager references, stdout/stderr
logging, resource limits, verified backup/restore, optional isolated Codex
workers, and disabled external integration writes by default.

### Backup/Restore Quickstart

Create and verify a backup, restore it, and run the V2.0 DR drill:

```bash
uv run molecule-ranker platform backup --output backups/platform-backup.zip
uv run molecule-ranker platform backup-verify backups/platform-backup.zip
uv run molecule-ranker platform restore --input backups/platform-backup.zip --target-dir restored/
uv run molecule-ranker platform dr-drill --output-dir dr_drill/
```

The DR drill creates a backup, verifies its manifest and artifact hashes,
restores to a temporary environment, checks database migration compatibility,
loads key projects and artifacts, validates user/role metadata, confirms
secrets are excluded, runs a smoke workflow, and emits a DR report.

## Docs Index

- V2 overview: `docs/v2/index.md`
- V2 quickstart: `docs/v2/quickstart.md`
- V2 architecture: `docs/v2/architecture.md`
- V2 security model: `docs/v2/security_model.md`
- V2 deployment: `docs/v2/deployment.md`
- V2 admin guide: `docs/v2/admin_guide.md`
- V2 user guide: `docs/v2/user_guide.md`
- V2 scientist guide: `docs/v2/scientist_guide.md`
- V2 reviewer guide: `docs/v2/reviewer_guide.md`
- V2 operator guide: `docs/v2/operator_guide.md`
- V2 integration guide: `docs/v2/integration_guide.md`
- V2 Codex backbone: `docs/v2/codex_backbone.md`
- V2 Codex runtime agent: `docs/v2/codex_runtime_agent.md`
- V2 data governance: `docs/v2/data_governance.md`
- V2 validation package: `docs/v2/validation_package.md`
- V2 backup/restore: `docs/v2/backup_restore.md`
- V2 troubleshooting: `docs/v2/troubleshooting.md`
- V2 limitations: `docs/v2/limitations.md`
- V2 release notes: `docs/v2/release_notes.md`
- User overview: `docs/user/overview.md`
- Ranking workflow: `docs/user/ranking_workflow.md`
- Generated molecules: `docs/user/generated_molecules.md`
- Developability: `docs/user/developability.md`
- Literature evidence: `docs/user/literature_evidence.md`
- Experimental feedback: `docs/user/experimental_feedback.md`
- Review workflow: `docs/user/review_workflow.md`
- Active learning: `docs/user/active_learning.md`
- Integrations: `docs/user/integrations.md`
- Codex assistant: `docs/user/codex_assistant.md`
- Evaluation benchmarks: `docs/user/evaluation_benchmarks.md`
- Dashboard: `docs/user/dashboard.md`
- Knowledge graph: `docs/user/knowledge_graph.md`
- Limitations: `docs/user/limitations.md`
- Admin users and roles: `docs/admin/users_and_roles.md`
- Admin security checklist: `docs/admin/security_checklist.md`
- V1.0 API and artifact contracts: `docs/contracts/v1.0-api-and-artifacts.md`
- V2.0 enterprise contracts: `docs/contracts/v2.0-enterprise-contracts.md`

## Training Index

- Admin training: `docs/training/admin_training.md`
- Scientist training: `docs/training/scientist_training.md`
- Reviewer training: `docs/training/reviewer_training.md`
- Operator training: `docs/training/operator_training.md`
- Integration admin training: `docs/training/integration_admin_training.md`
- Codex guardrails training: `docs/training/codex_guardrails_training.md`
- Generated molecule interpretation: `docs/training/generated_molecule_interpretation.md`
- Model prediction interpretation: `docs/training/model_prediction_interpretation.md`
- Evaluation interpretation: `docs/training/evaluation_interpretation.md`

## Runbooks Index

- Deployment: `docs/runbooks/deployment.md`
- Local development: `docs/runbooks/local_development.md`
- Production config: `docs/runbooks/production_config.md`
- Backup and restore: `docs/runbooks/backup_restore.md`
- Worker operations: `docs/runbooks/worker_operations.md`
- Codex worker: `docs/runbooks/codex_worker.md`
- Integration sync: `docs/runbooks/integration_sync.md`
- Security incidents: `docs/runbooks/security_incidents.md`
- Data retention: `docs/runbooks/data_retention.md`
- Troubleshooting: `docs/runbooks/troubleshooting.md`
- Release process: `docs/runbooks/release_process.md`

## V1.0 Hosted Mode

Start the local API surface as before:

```bash
uv run molecule-ranker project serve --root . --host 127.0.0.1 --port 8765
```

Start the hosted MVP surface for internal use:

```bash
uv run molecule-ranker project serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Use `--platform-database-url sqlite:////absolute/path/platform.sqlite` when a
database URL is easier to inject from deployment configuration. For hosted
production, use a PostgreSQL URL such as
`postgresql+psycopg://user:password@host:5432/molecule_ranker`.

Hosted mode is intended for internal research teams. It requires bearer-token
authentication for project, artifact, audit, job, dashboard, and admin routes.
Create/bootstrap users through the CLI/API or by initializing the platform
database in application code. Artifact files may stay on local/object storage;
the platform database stores paths, hashes, and provenance metadata.

Database lifecycle commands:

```bash
uv run molecule-ranker db init --db-path .molecule-ranker/platform.sqlite
uv run molecule-ranker db migrate --database-url "$MOLECULE_RANKER_DATABASE_URL"
uv run molecule-ranker db check --database-url "$MOLECULE_RANKER_DATABASE_URL"
```

The platform database never stores plaintext passwords, API keys, Codex/ChatGPT
credentials, or unredacted secret-like audit metadata.

Authentication commands:

```bash
uv run molecule-ranker user create \
  --email admin@example.com \
  --password 'Strong-password-1' \
  --admin \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker user list --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker auth token create \
  --name automation \
  --user-id user-... \
  --created-by-user-id user-... \
  --scope project:read \
  --scope run:create \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker auth token revoke \
  --token-id sat-... \
  --actor-user-id user-... \
  --db-path .molecule-ranker/platform.sqlite
```

### V1.0 Platform Controls

Authentication modes:

- `local_password`: internal/self-hosted deployments with strong password
  hashing and expiring bearer tokens.
- Service account tokens: automation tokens are hashed at rest, scoped, and
  shown only at creation time.
- OIDC: configurable issuer/client placeholders are present, but OIDC is
  optional and disabled cleanly when not configured.

RBAC is enforced for hosted API routes, dashboard pages, artifact downloads,
jobs, Codex tasks, and admin controls. Organization roles are `owner`, `admin`,
`scientist`, `reviewer`, `viewer`, and `service_account`. Project roles are
`project_owner`, `editor`, `reviewer`, `viewer`, and `runner`. Permission checks
cover `project:create`, `project:read`, `project:update`, `project:delete`,
`run:create`, `run:read`, `run:cancel`, `artifact:read`, `artifact:export`,
`review:read`, `review:write`, `experiment:import`, `experiment:read`,
`codex:run`, `codex:read`, `admin:manage_users`, `admin:manage_org`, and
`admin:view_audit`. Viewers are read-only, reviewers can review/comment without
changing project config, runners can enqueue/run jobs without managing users,
and Codex work requires explicit `codex:run` permission. Admin actions still
write audit events.

The hosted dashboard is server-rendered and login-gated. It includes project and
run views, ranking tables, generated molecule tables, developability,
experimental results, active learning, review queues, candidate dossiers, Codex
assistant output, audit logs, notifications, and admin pages. Every dashboard
view keeps research-use disclaimers visible, labels generated molecules as
computational hypotheses, separates experimental evidence from model
predictions, and shows Codex output separately from evidence.

Codex CLI remains the LLM backbone, but hosted mode never invokes arbitrary
API-triggered shell execution. Hosted Codex work is queued as a project job and
executed by a controlled `CodexWorker` with scoped artifact context, isolated
working directories, allowlisted task types, transcript redaction, and
pre/post-guardrails. Codex outputs are not evidence, assay results, molecules,
review decisions, or scores.

Observability includes structured JSON logs, request IDs, job IDs,
project/run IDs, health/readiness/version endpoints, audit logs, and metrics
such as `pipeline_runs_total`, `jobs_queued_total`, `jobs_failed_total`,
`codex_tasks_total`, `codex_guardrail_failures_total`,
`artifacts_written_total`, `auth_failures_total`, and
`api_request_duration_seconds`. Logs and metrics must not include passwords,
API keys, service tokens, Codex credentials, full imported assay files, or full
copyrighted article text.

Data governance controls include project export packages, user/project export
metadata, soft delete by default, hard delete only with explicit project-ID
confirmation, artifact retention, Codex transcript retention, audit retention,
cache retention, and assay-result retention. Exports exclude secrets and cache
files and include artifact manifests with hashes.

Deployment options:

- Local CLI mode: regular `molecule-ranker rank`, project, review, experiment,
  and Codex CLI commands still work without hosted services.
- Local web mode: run `molecule-ranker serve` on `127.0.0.1` with SQLite.
- Docker Compose internal deployment: use the provided compose files with
  mounted artifact/project storage and secrets supplied through environment
  variables.
- Optional Kubernetes manifests: deployment examples are provided under
  `deployment/k8s/` for teams that already operate Kubernetes.

### V1.0 Usage Examples

Create an admin user:

```bash
uv run molecule-ranker user create \
  --email admin@example.com \
  --password 'Strong-password-1' \
  --display-name "Platform Admin" \
  --admin \
  --db-path .molecule-ranker/platform.sqlite
```

Start the hosted server on localhost:

```bash
uv run molecule-ranker serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Start a background worker:

```bash
uv run molecule-ranker worker run \
  --db-path .molecule-ranker/platform.sqlite
```

Create a project in local CLI mode:

```bash
uv run molecule-ranker project create \
  --root ./research/parkinsons \
  --workspace-id parkinsons \
  --name "Parkinson Research"
```

Run a source-backed project job locally and register the output:

```bash
uv run molecule-ranker rank "Parkinson disease" \
  --top 10 \
  --output-dir ./research/parkinsons/results

uv run molecule-ranker project run \
  ./research/parkinsons/results/parkinson-disease \
  --root ./research/parkinsons \
  --run-id run-001
```

Open the hosted dashboard after login:

```bash
open http://127.0.0.1:8765/dashboard
```

Create a scoped service account token:

```bash
uv run molecule-ranker auth token create \
  --name automation \
  --user-id user-service-account \
  --created-by-user-id user-admin \
  --scope project:read \
  --scope run:create \
  --scope artifact:read \
  --db-path .molecule-ranker/platform.sqlite
```

Queue a hosted Codex-backed project summary. The API enqueues the job; a worker
runs Codex later through `CodexWorker`.

```bash
TOKEN="$(curl -s http://127.0.0.1:8765/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"Strong-password-1"}' \
  | jq -r '.access_token')"

curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8765/projects/parkinsons/codex/summarize
```

Export a project package:

```bash
uv run molecule-ranker platform export-project parkinsons \
  --output ./exports/parkinsons-export.zip \
  --actor-user-id user-admin \
  --db-path .molecule-ranker/platform.sqlite
```

Run retention cleanup. Defaults perform no automatic deletion unless retention
days are configured.

```bash
uv run molecule-ranker platform retention run \
  --actor-user-id user-admin \
  --cache-retention-days 7 \
  --codex-transcript-retention-days 90 \
  --db-path .molecule-ranker/platform.sqlite
```

Deploy with Docker Compose:

```bash
cp .env.example .env
# edit .env with real secrets and database settings
docker compose up --build
```

## V1.0 External Integrations

V1.0 hardens the guarded external integration framework for internal research-system
handoffs and imports. Supported connector categories are:

- ELN/LIMS systems.
- Compound registries.
- Assay result providers.
- Generic REST APIs.
- Generic local/mounted file drops and SFTP-style staging interfaces.
- PostgreSQL-compatible data warehouses.
- Signed webhooks.

Benchling is the first concrete ELN/LIMS and registry connector. Generic REST
and generic file connectors cover systems that do not yet have a custom
connector. Warehouse exports support curated molecule-ranker tables such as
`mr_candidates`, `mr_generated_molecules`, `mr_targets`,
`mr_evidence_items`, `mr_literature_claims`,
`mr_developability_assessments`, `mr_assay_results`,
`mr_review_decisions`, `mr_active_learning_suggestions`, `mr_sync_jobs`, and
`mr_artifacts`. Databricks SQL and Snowflake connectors are optional dependency
paths. SiLA is present only as a metadata adapter placeholder; V1.0 does not
control instruments or devices.

Integration safety boundaries:

- Default connector mode is `read_only` or `dry_run`.
- External writes and exports require explicit `write_enabled` mode and
  permission.
- Credentials are stored as secret references, such as `env:BENCHLING_API_KEY`,
  not plaintext secret values.
- Secrets are redacted from logs, audit records, dashboards, artifacts, exports,
  and Codex prompts.
- Codex can assist with schema-mapping suggestions, sync-failure explanation,
  mapping-review questions, and export summaries, but it cannot activate
  mappings, write to external systems, invent external IDs, invent Benchling
  records, invent assay runs/results, or create evidence.
- Imported assay data must pass validation before it can become experimental
  evidence or affect scores.
- Export packages and integration outputs do not include lab protocols,
  synthesis instructions, dosing, patient treatment guidance, or clinical
  advice.

### Integration CLI Examples

Create an external system:

```bash
uv run molecule-ranker integration system create \
  --name "Benchling Dev" \
  --system-type eln \
  --vendor benchling \
  --base-url https://benchling.example \
  --mode dry_run \
  --db-path .molecule-ranker/platform.sqlite
```

Create a credential reference. The secret value stays in the environment; the
database stores only `env:BENCHLING_API_KEY`.

```bash
export BENCHLING_API_KEY="..."

uv run molecule-ranker integration credential create \
  --external-system-id ext-benchling-dev \
  --credential-type api_key \
  --secret-env-var BENCHLING_API_KEY \
  --root . \
  --json
```

Health check an integration configuration:

```bash
uv run molecule-ranker integration system health ext-benchling-dev \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Run a dry-run sync. This records a guarded sync job and does not write to an
external system.

```bash
uv run molecule-ranker integration sync run \
  --external-system-id ext-benchling-dev \
  --direction import \
  --object-type assay_results \
  --project-id project-1 \
  --dry-run \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Benchling registry mapping is handled as a pending mapping review item. Codex
may suggest a mapping, but deterministic validation and a human approval step
are required before activation.

```bash
uv run molecule-ranker integration mapping list \
  --project-id project-1 \
  --status pending_review \
  --db-path .molecule-ranker/platform.sqlite \
  --json

uv run molecule-ranker integration mapping approve mapping-123 \
  --db-path .molecule-ranker/platform.sqlite
```

Prepare a Benchling assay-result import in dry-run mode:

```bash
uv run molecule-ranker integration benchling import-assay-results \
  --external-system-id ext-benchling-dev \
  --project-id project-1 \
  --dry-run \
  --json
```

Import assay results from a generic file drop. The file connector reads only
inside its configured root, blocks path traversal, and validates imported assay
results before they can affect scoring.

```bash
uv run molecule-ranker integration sync run \
  --external-system-id ext-generic-file \
  --direction import \
  --object-type assay_results \
  --project-id project-1 \
  --dry-run \
  --db-path .molecule-ranker/platform.sqlite
```

Export curated warehouse tables as local CSV artifacts:

```bash
uv run molecule-ranker integration warehouse export \
  --project-id project-1 \
  --external-system-id ext-warehouse \
  --tables candidates,assay_results,review_decisions \
  --format csv \
  --dry-run \
  --output-dir .molecule-ranker/warehouse-export \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Inspect a sync job:

```bash
uv run molecule-ranker integration sync show sync-... \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Generate a signed webhook test payload:

```bash
uv run molecule-ranker integration webhook test \
  --external-system-id ext-benchling-dev \
  --secret "$WEBHOOK_SIGNING_SECRET" \
  --event-type assay_result.created \
  --json
```

API authentication uses bearer tokens. `POST /auth/login` returns a short-lived
access token plus a refresh token; `POST /auth/refresh` issues a new access
token; `POST /auth/logout` invalidates the session. Browser cookie sessions are
not used in V1.0, so CSRF protection is not part of the API bearer-token flow.
OIDC settings are present as placeholders and `/auth/oidc/*` routes return a
clean disabled response unless configured.

## V1.0 Codex CLI Orchestration

V1.0 keeps Codex CLI as the primary LLM agent backbone for molecule-ranker. Codex
CLI is used because OpenAI documents Codex as included with ChatGPT Plus, Pro,
Business, Enterprise/Edu, and other eligible plans, and the CLI can run through
local ChatGPT authentication instead of requiring project-specific OpenAI API
keys. Usage is still subject to the user's ChatGPT/Codex plan limits and local
Codex configuration. See OpenAI's Codex plan documentation:
<https://help.openai.com/en/articles/11369540-codex-in-chatgpt>.

Codex CLI orchestrates, summarizes, explains, compares, and plans. It can
inspect local molecule-ranker artifacts, draft source-grounded summaries, build
review-assistant questions, explain uncertainty, compare runs or candidates,
plan safe follow-up CLI tasks, and help with engineering test/lint/typecheck
loops.

Codex CLI is not the biomedical truth layer. Deterministic source-backed tools
remain authoritative for disease resolution, target evidence, molecule
retrieval, literature evidence, generated hypotheses, developability triage,
experimental result ingestion, assay-result linking, and score recalibration.
Codex-backed outputs are stored separately as `codex_backbone` artifacts and do
not directly alter candidates, evidence, assay results, generated molecules,
review decisions, or score fields.

Codex CLI must not:

- Create biomedical evidence.
- Directly change scores.
- Fabricate citations, assay results, targets, molecules, evidence, or paper
  claims.
- Provide medical advice, synthesis instructions, lab protocols, animal or
  human dosing, or patient treatment guidance.
- Claim that a molecule cures, treats, binds, is active, is safe, is effective,
  or is synthesizable unless that claim is explicitly present in source-backed
  artifacts and still framed as artifact context rather than a new conclusion.

Guardrails and evals are included. Prompt and output checks redact secrets,
exclude cache/secret artifacts, reject fabrication requests, flag fake
citations and assay results, block protocol/synthesis/dosing/treatment content,
validate JSON, and evaluate artifact grounding and safe command planning.

Register runs in a local project workspace:

```bash
uv run molecule-ranker project init --root .
uv run molecule-ranker project register-run results/example-condition --run-id run-1
uv run molecule-ranker project list
```

Compare registered runs and generate a dashboard:

```bash
uv run molecule-ranker project compare --run-id run-1 --run-id run-2
uv run molecule-ranker project dashboard --output-dir .molecule-ranker/dashboard
```

Start the local JSON API server:

```bash
uv run molecule-ranker project serve --host 127.0.0.1 --port 8765
```

Check local Codex CLI status without printing credentials:

```bash
uv run molecule-ranker codex status --json
```

Summarize a completed run with Codex:

```bash
uv run molecule-ranker codex summarize-run \
  results/alzheimer-disease/ \
  --dry-run \
  --json
```

Explain a candidate using existing artifacts only:

```bash
uv run molecule-ranker codex explain-candidate \
  results/alzheimer-disease/ \
  --candidate "ExampleCandidate" \
  --dry-run \
  --json
```

Plan a safe follow-up run:

```bash
uv run molecule-ranker codex plan-followup \
  results/alzheimer-disease/ \
  --dry-run \
  --json
```

Compare two run directories:

```bash
uv run molecule-ranker codex compare-runs \
  results/alzheimer-disease-run-a/ \
  results/alzheimer-disease-run-b/ \
  --dry-run \
  --json
```

Run Codex evals:

```bash
uv run molecule-ranker codex eval \
  --cases tests/fixtures/codex_eval_cases.json \
  --json
```

Run an engineering test-loop analysis with Codex:

```bash
uv run molecule-ranker codex test-loop \
  --test-output test_output.txt \
  --json
```

## V0.5 Expert Review Workflow

V0.5 adds an optional expert review workflow and human-in-the-loop triage system.
The ranking pipeline still works without review enabled. When enabled, V0.5
creates a local `ReviewWorkspace` backed by SQLite, writes `review_queue.json`,
and can optionally generate a local static HTML dashboard.

The review workflow is local tooling, not a multi-user production system. The
SQLite review database defaults to `.review/molecule-ranker-review.sqlite` and
stores local reviewer identity metadata only; it does not add authentication,
authorization, collaboration controls, or a SaaS deployment model.

Review decisions are stored separately from scientific evidence and model
scores. They are expert triage labels, not biomedical evidence, clinical
conclusions, or proof of safety, efficacy, binding, or synthesizability.
Expert feedback can inform future prioritization only when
`enable_feedback_prior` or `--enable-feedback-prior` is explicitly enabled, and
it remains labeled as expert review feedback rather than experimental evidence.

Candidate dossiers summarize evidence, risks, uncertainty, source provenance,
limitations, reviewer decisions, comments, and follow-up requests. Validation
handoff packets are high-level expert-planning packets: they can name broad
validation categories such as target engagement, cellular pathway, phenotype,
or toxicology triage, but they do not include lab protocols, operational steps,
synthesis instructions, reagents, reaction conditions, temperatures, dosing, or
patient treatment instructions. No clinical advice, dosage, or treatment
instructions are provided anywhere in the review workflow.

Generated molecules remain computational hypotheses. They have no direct
experimental evidence, are not claimed to be active, and remain labeled as
generated throughout review queues, dossiers, comparisons, handoffs, exports,
reports, and dashboards.

Review objects include `ReviewWorkspace`, `ReviewQueue`, `ReviewItem`,
`ReviewerDecision`, `ExpertFeedback`, `CandidateDossier`, `ValidationHandoff`,
`ReviewAuditLog`, `FeedbackIngestionAgent`, and `DossierWriterAgent`.

Run ranking with the review workflow enabled:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-name "Local Reviewer" \
  --reviewer-role medicinal_chemist \
  --max-review-items 100 \
  --generate-review-dashboard
```

Create a review workspace from existing run artifacts:

```bash
uv run molecule-ranker review create \
  --from-run results/alzheimer-disease/ \
  --db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-name "Local Reviewer" \
  --reviewer-role medicinal_chemist
```

List review workspaces:

```bash
uv run molecule-ranker review list \
  --db-path .review/molecule-ranker-review.sqlite
```

Show a review item:

```bash
uv run molecule-ranker review item \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite
```

Make a review decision:

```bash
uv run molecule-ranker review decide \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite \
  --decision needs_more_data \
  --rationale "Expert triage label only; request more disease-specific evidence." \
  --reviewer-id reviewer-1 \
  --confidence 0.7 \
  --factor weak_literature
```

Add a reviewer comment:

```bash
uv run molecule-ranker review comment \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite \
  --comment "Check whether the target rationale is disease-specific." \
  --comment-type evidence_question \
  --reviewer-id reviewer-1
```

Compare candidates side by side:

```bash
uv run molecule-ranker review compare \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  review-item-run-1-generated-maob-001 \
  --db-path .review/molecule-ranker-review.sqlite
```

Generate a candidate dossier:

```bash
uv run molecule-ranker review dossier \
  --workspace results/alzheimer-disease/review_queue.json \
  --item-id review-item-run-1-chembl123 \
  --output results/alzheimer-disease/dossiers/chembl123.md
```

Create a validation handoff packet:

```bash
uv run molecule-ranker review handoff \
  --workspace results/alzheimer-disease/review_queue.json \
  --item-id review-item-run-1-chembl123 \
  --reviewer-id reviewer-1 \
  --output results/alzheimer-disease/validation_handoffs/chembl123.json
```

Export a review package:

```bash
uv run molecule-ranker review export \
  workspace-run-1-alzheimer-disease \
  --db-path .review/molecule-ranker-review.sqlite \
  --format zip \
  --output results/alzheimer-disease/review_export.zip
```

Generate a local static dashboard:

```bash
uv run molecule-ranker review dashboard \
  workspace-run-1-alzheimer-disease \
  --db-path .review/molecule-ranker-review.sqlite \
  --output results/alzheimer-disease/review_dashboard/
```

## V0.6 Experimental Feedback Loop

V0.6 adds a local experimental feedback loop and active-learning prioritization
from assay result files. Assay results are imported by the user from CSV or JSON
files; the software does not fabricate assay results, infer assay outcomes from
model scores, or treat missing result data as evidence.

Experimental evidence is kept separate from database evidence, literature
evidence, expert review feedback, and model predictions. Imported assay results
can affect candidate scores only when they are linked to the candidate or exact
generated structure and satisfy the configured QC policy. Failed-QC and
inconclusive results are recorded for provenance, summaries, and audit trails,
but they do not add score support.

In-vitro, biochemical, cellular, safety, and developability assay results do
not imply clinical efficacy, clinical safety, cure, treatment, or patient
benefit. Generated molecules gain direct experimental evidence only from exact
linked imported results for the tested structure; seed-molecule and analog
results are not generalized to generated molecules. Active-learning suggestions
are expert-triage suggestions only, not instructions to run experiments.

V0.6 does not provide lab protocols, synthesis instructions, synthesis routes,
reagent recipes, animal or human dosing, or patient treatment guidance. Optional
surrogate models are local estimates for prioritization only and are not
experimental evidence.

Safe neutral import templates are available under `templates/`:

```bash
templates/assay_results_template.csv
templates/assay_results_template.json
templates/README.md
```

Import an assay CSV:

```bash
uv run molecule-ranker experiment import templates/assay_results_template.csv \
  --db-path .experiments/results.sqlite \
  --imported-by researcher-1
```

Dry-run an import without writing the database:

```bash
uv run molecule-ranker experiment import templates/assay_results_template.csv \
  --db-path .experiments/results.sqlite \
  --dry-run \
  --json
```

List imported assay results:

```bash
uv run molecule-ranker experiment list \
  --db-path .experiments/results.sqlite \
  --target-symbol EXAMPLE \
  --outcome-label positive
```

Summarize imported results for a candidate:

```bash
uv run molecule-ranker experiment summarize \
  --candidate-name ExampleCandidateA \
  --db-path .experiments/results.sqlite
```

Link imported results to a saved run artifact:

```bash
uv run molecule-ranker experiment link \
  --from-run results/example-condition/ \
  --db-path .experiments/results.sqlite \
  --json
```

Rank with experimental evidence enabled:

```bash
uv run molecule-ranker rank "Example condition" \
  --top 10 \
  --enable-experimental-evidence \
  --experimental-db-path .experiments/results.sqlite \
  --strict-experimental-linking \
  --require-qc-passed-for-score
```

Generate an active-learning batch for expert triage:

```bash
uv run molecule-ranker experiment active-learning \
  --from-run results/example-condition/ \
  --db-path .experiments/results.sqlite \
  --strategy balanced \
  --batch-size 10 \
  --include-generated \
  --json
```

Export experimental results:

```bash
uv run molecule-ranker experiment export \
  --db-path .experiments/results.sqlite \
  --output results/example-condition/experimental_results_export.json
```

Create a high-level experimental report:

```bash
uv run molecule-ranker experiment report \
  --db-path .experiments/results.sqlite \
  --from-run results/example-condition/
```

## Install

Python 3.11+ is required. The repository is configured for `uv`:

```bash
uv sync
```

To verify the command is available:

```bash
uv run molecule-ranker --help
uv run molecule-ranker rank --help
```

## CLI Usage

Run normal ranking without generated molecules:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-generation
```

Generation is disabled by default, so the same behavior is used when no
generation option is supplied:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Normal V0.6 ranking includes PubMed literature retrieval and developability
triage by default:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-literature \
  --literature-source pubmed \
  --openalex-enrichment
```

Run normal ranking with developability controls made explicit:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-developability \
  --developability-filter-mode filter_generated_only \
  --reject-critical-alerts
```

Run without literature evidence when you only want database-derived ranking:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-literature
```

Disable developability triage when you only want the V0.1-V0.3 evidence and
generation behavior:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-developability
```

Use strict literature mode when PubMed availability is required for the run:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --strict-literature
```

Write the normal report files and print a JSON summary:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --json
```

Run ranking with target-conditioned generated molecule hypotheses:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --enable-developability \
  --max-generation-objectives 3 \
  --generated-per-objective 10 \
  --max-retained-generated 10 \
  --generation-random-seed 123
```

Run ranking with the optional V0.5 review workflow:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-role medicinal_chemist \
  --generate-review-dashboard
```

Run the generation-focused command. It still runs disease, target, molecule,
and literature retrieval first, but focuses the terminal output on generated
hypotheses:

```bash
uv run molecule-ranker generate "Alzheimer disease" \
  --top 10 \
  --max-retained-generated 25 \
  --generation-random-seed 123
```

Run developability assessment later from an existing candidate artifact without
rerunning disease, target, molecule, or literature retrieval:

```bash
uv run molecule-ranker assess-developability \
  --input results/alzheimer-disease/generated_candidates.json
```

Print a JSON CLI summary for a run that includes generated molecules. The
summary includes generated counts and output paths; generated structures are
written to `generated_candidates.json`.

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --max-retained-generated 10 \
  --json
```

Inspect the retained generated structures from the generated JSON artifact:

```bash
jq '.retained_generated_molecules[] | {generated_id, canonical_smiles, inchi_key, generation_score}' \
  results/alzheimer-disease/generated_candidates.json
```

Benchmark a generated-molecule artifact with internal V1.1 quality metrics:

```bash
uv run molecule-ranker benchmark-generation \
  --input results/alzheimer-disease/generated_candidates.json
```

Benchmark a V0.4 developability artifact with internal coverage and calibration
metrics:

```bash
uv run molecule-ranker benchmark-developability \
  --input results/alzheimer-disease/developability.json
```

Enable optional target structure metadata retrieval. This retrieves structure
metadata for computational triage; it does not require docking:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-structure-retrieval \
  --max-structures-per-target 5
```

Enable optional docking only when you explicitly want the docking plugin path.
Docking is disabled by default, docking inputs must be reviewed, and docking
scores do not prove binding:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-structure-retrieval \
  --enable-docking \
  --max-docked-molecules 5
```

Useful options:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --use-cache \
  --cache-dir .cache/molecule-ranker \
  --cache-ttl-hours 24 \
  --max-targets 25 \
  --max-molecules-per-target 10 \
  --max-activity-records-per-target 10 \
  --max-indications-per-molecule 20 \
  --max-warnings-per-molecule 20 \
  --max-literature-queries 100 \
  --max-papers-per-query 10 \
  --max-targets-for-literature 10 \
  --max-candidates-for-literature 20 \
  --enable-generation \
  --generation-method generator_ensemble \
  --max-seed-molecules 20 \
  --max-generation-objectives 10 \
  --generated-per-objective 50 \
  --max-retained-generated 50 \
  --generation-random-seed 123 \
  --include-generated-in-main-ranking \
  --reject-basic-alerts \
  --enable-developability \
  --strict-developability \
  --developability-filter-mode filter_generated_only \
  --reject-critical-alerts \
  --reject-high-toxicity-risk \
  --enable-structure-retrieval \
  --max-structures-per-target 5 \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-role medicinal_chemist \
  --max-review-items 100 \
  --include-generated-in-review \
  --generate-review-dashboard \
  --enable-experimental-evidence \
  --experimental-db-path .experiments/results.sqlite \
  --strict-experimental-linking \
  --require-qc-passed-for-score \
  --ncbi-email researcher@example.org \
  --ncbi-api-key-env NCBI_API_KEY \
  --max-retries 3 \
  --retry-backoff-seconds 0.5 \
  --verbose
```

By default, ranking requests use the live public APIs first and write successful
real JSON responses to the configured cache directory. Cached responses are not
read as an offline substitute unless `--use-cache` is explicitly passed; that
mode is a cached-real-data fallback for previously retrieved successful
responses. Use `--no-cache` to bypass cache reads and writes.

Important V0.6 configuration options map to typed `RankerConfig` fields:

- `results_dir`, `cache_dir`: output and successful-real-response cache locations.
- `use_cache`: enables cache writes; disabled by `--no-cache`.
- `allow_cached_real_data`: enables cached-real-data fallback; enabled by `--use-cache`.
- `cache_ttl_seconds`: TTL for cached successful real responses.
- `default_top`: ranked candidates retained.
- `default_target_limit`: evidence-backed targets retained after target discovery.
- `target_source_limit`: Open Targets source retrieval size before local filtering.
- `max_molecules_per_target`: ChEMBL mechanism/molecule records retained per target.
- `max_activity_records_per_target`: ChEMBL activity records retained per target.
- `max_indications_per_molecule`, `max_warnings_per_molecule`: ChEMBL clinical and warning context retained per molecule.
- `enable_literature`: includes or skips literature retrieval.
- `strict_literature`: if `false`, source failures are warned and the run
  continues when database evidence is sufficient; if `true`, literature source
  failures stop the run.
- `literature_sources`: currently supports `pubmed`; PubMed is the primary
  literature evidence source.
- `enable_openalex_enrichment`: enables optional OpenAlex citation,
  open-access, topic, and retraction enrichment.
- `max_literature_queries`, `max_papers_per_query`: literature query and paper limits.
- `max_targets_for_literature`, `max_candidates_for_literature`: entity limits for literature query generation.
- `ncbi_tool`, `ncbi_email`, `ncbi_api_key`: NCBI E-utilities identification and optional API-key configuration.
- `literature_request_timeout_seconds`, `literature_max_retries`, `literature_cache_ttl_seconds`: literature adapter request behavior.
- `request_timeout_seconds`, `max_retries`, `retry_backoff_seconds`: live API request behavior.
- `strict_enrichment`: records strict enrichment intent for runs that should treat optional enrichment more conservatively.
- `enable_generation`: opt-in switch for generated molecule hypotheses.
- `strict_generation`: fails when enabled generation cannot produce retained
  hypotheses; default mode warns and continues.
- `include_generated_in_main_ranking`: optionally includes generated hypotheses
  in the main ranking while preserving `origin="generated"` and no direct
  evidence.
- `generation_method`: generated molecule backend; V1.1 defaults to
  `generator_ensemble`. Individual backends such as `selfies_mutation` remain
  available for compatibility.
- `generation_random_seed`: optional deterministic random seed.
- `max_seed_molecules`, `max_generation_objectives`, `generated_per_objective`,
  `max_generated_before_filtering`, `max_retained_generated`: generation size
  and retention controls.
- `duplicate_similarity_threshold`, `near_duplicate_similarity_threshold`,
  `distant_similarity_threshold`, `reject_distant_generated`: novelty and
  target-conditioning filters using Morgan-fingerprint Tanimoto similarity.
- `reject_basic_alerts`, `allowed_generation_elements`: coarse chemistry sanity
  filters for generated structures.
- `enable_developability`: enables V0.4 developability assessment by default.
- `strict_developability`: fails the run when developability assessment fails
  for a molecule instead of recording an unknown-risk assessment.
- `assess_existing_molecules`, `assess_generated_molecules`: choose which
  molecule classes receive developability triage.
- `developability_filter_mode`: action mode; existing evidence-backed
  molecules are not silently removed by default, while generated molecules may
  be filtered more aggressively.
- `reject_critical_alerts`, `reject_high_toxicity_risk`, `alert_mode`: control
  how chemistry alerts and toxicity-risk flags affect recommendations and
  filtering.
- `enable_rule_based_admet`, `enable_local_admet_models`,
  `allow_rule_based_admet_fallback`: ADMET triage controls. The default
  rule-based ADMET baseline is a computational triage heuristic and does not
  prove safety.
- `enable_synthesizability`: enables coarse synthesizability scoring. It does
  not provide synthesis routes or practical synthesis instructions.
- `enable_structure_retrieval`: optionally retrieves target structure metadata.
- `enable_docking`: optionally runs docking plugin paths. It is disabled by
  default and docking scores do not prove binding.
- `strict_structure_mode`, `write_docking_artifacts`,
  `max_structures_per_target`, `max_docked_molecules`: optional
  structure/docking behavior controls.
- `enable_structure_filtering`: records structure-aware developability filter
  pass/fail fields.
- `filter_developability_failures`: optionally removes candidates that fail the
  configured developability threshold.
- `min_developability_score`: threshold for optional structure-aware filtering.
- `enable_tdc_benchmark`, `tdc_data_dir`: optional benchmark controls for local
  ADMET model evaluation if TDC is installed and explicitly enabled.
- `enable_review_workflow`: opt-in switch for local expert review workspace
  creation during ranking or generation.
- `review_db_path`: local SQLite path for review workspaces. This is local
  persistence, not a multi-user production database.
- `reviewer_id`, `reviewer_name`, `reviewer_role`: optional local reviewer
  identity metadata.
- `max_review_items`: maximum review queue size.
- `include_generated_in_review`: includes generated hypotheses in the review
  queue while preserving `candidate_origin="generated"`.
- `generated_high_priority_allowed`: controls whether generated hypotheses may
  receive high-priority review buckets; disabled by default.
- `review_priority_policy`: review queue prioritization policy; default is
  conservative.
- `enable_feedback_prior`: explicitly enables expert feedback as future
  prioritization context. Disabled by default.
- `feedback_db_path`, `feedback_weight`: local feedback store and weighting for
  feedback-prior behavior.
- `generate_review_dashboard`, `review_dashboard_dir`: optional static local
  dashboard generation.
- `enable_experimental_evidence`: opt-in switch for imported assay result
  linking and scoring.
- `experimental_db_path`: local SQLite path for imported assay results.
- `experimental_result_source_filter`: optional filter for imported result
  source labels such as `csv_import` or `json_import`.
- `require_qc_passed_for_score`: requires QC-passed imported results before
  score support is added. Failed-QC results remain auditable but do not improve
  scores.
- `include_inconclusive_results`: records inconclusive imported results in
  experimental summaries while keeping them non-score-promoting.
- `strict_experimental_linking`: requires exact candidate, InChIKey, canonical
  SMILES, generated ID, or review-item links by default.
- `enable_local_admet_models` and the optional `surrogate` dependency group:
  local surrogate models may estimate assay outcomes for prioritization, but
  those estimates are not experimental evidence and are not converted into
  `EvidenceItem` objects.

The effective config is serialized into `trace.json` so a run can be audited
with the limits, cache policy, and request policy that produced it. Defaults are
chosen for a first real run and do not reduce target discovery to a single
target.

Check live public adapter reachability without running a ranking job:

```bash
uv run molecule-ranker health
```

The health command probes Open Targets, ChEMBL, PubChem, PubMed, and OpenAlex with short request
timeouts and prints source, status, latency, endpoint, and any error. Health
checks are only run when this command is requested.

Run live public API smoke tests explicitly:

```bash
MOLECULE_RANKER_RUN_LIVE=1 uv run pytest -m live tests_live/
```

JSON summary output:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10 --json
```

Files are written under:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/generated_candidates.json
results/<disease_slug>/generated_molecules.json
results/<disease_slug>/generation_trace.json
results/<disease_slug>/developability.json
results/<disease_slug>/developability_report.md
results/<disease_slug>/developability_assessments.json
results/<disease_slug>/experimental_results.json
results/<disease_slug>/experimental_evidence.json
results/<disease_slug>/active_learning_batch.json
results/<disease_slug>/experimental_report.md
results/<disease_slug>/trace.json
```

`generated_candidates.json` is written when generation is enabled.
`generated_molecules.json` is a compatibility alias with the same payload. The
payload includes objectives, selected seeds, retained generated molecules,
rejected generated molecules with rejection reasons, generation warnings,
generation config, and limitations. Generated structures include SMILES and
InChIKey when available, but no synthesis instructions and no generated
`EvidenceItem` claims.

`developability.json` is written when developability is enabled or explicitly
disabled. When disabled it clearly reports `enabled=false` and `success=false`.
When enabled, it includes assessed counts, retained/deprioritized/rejected
counts, risk distribution, alert distribution, ADMET endpoint coverage,
individual assessments, warnings, limitations, config, and generation time.
Candidate artifacts include compact developability summaries; generated
candidate artifacts also include rejection reasons.

No static example biomedical result is included in this README because example
rankings should only be copied from an actual successful live run with its
retrieval timestamp and source provenance.

## Internet and API Assumptions

The CLI uses public internet APIs at runtime. A successful run assumes:

- Network access is available.
- Public sources are reachable and not rate-limited.
- Source schemas still match the adapter expectations.
- The queried disease can be resolved to a public disease entity.
- Evidence-backed targets and molecules exist in the queried sources.

If real data cannot be retrieved, the app fails instead of inventing results.

## Data Sources Used

Production adapters are isolated under `molecule_ranker/data_sources/`:

- Open Targets: disease resolution and disease-target association evidence.
- ChEMBL: target-linked existing molecules, mechanisms, activities, assays,
  indications, drug warnings, and development status where available.
- PubChem: molecule identifier and chemical metadata enrichment where available.
- PubMed: real paper records and abstracts via NCBI E-utilities.
- OpenAlex: optional citation count, open-access, and retraction metadata.
- RCSB PDB: optional target structure metadata when explicitly enabled.
- AlphaFold DB: optional predicted target structure metadata when explicitly
  enabled.

HTTP requests are made only inside adapter classes. Tests may mock adapter
responses, but production code does not import test fixtures or ship fixture
biomedical knowledge.

## Generated Molecule Hypotheses

V0.3 added target-conditioned novel molecule generation as an opt-in workflow.
Generation is off for ordinary ranking runs unless the user passes
`--enable-generation` or uses `molecule-ranker generate`.

The V1.1 design generation pipeline:

1. Builds a source-grounded design plan from existing run artifacts.
2. Selects real retrieved existing molecules as seeds and derives scaffold
   context with deterministic validation.
3. Builds machine-readable design objectives for evidence-backed targets with
   selected seeds.
4. Allocates budget across the V1.1 generator ensemble and records independent
   generator provenance, warnings, and failures.
5. Treats scaffold hops, fragment growth, matched-pair transforms, and
   reactionless analog enumeration as computational transformations only, not
   synthesis plans or activity claims.
6. Canonicalizes SMILES, computes InChIKey when possible, descriptors,
   fingerprints, and Tanimoto similarity.
7. Filters invalid, duplicate, near-duplicate, distant, and chemically
   unreasonable structures using coarse generation rules.
8. Applies inspectable oracle scoring, uncertainty estimation, medicinal
   chemistry critique, and experiment-readiness ranking.
9. Reports retained and rejected generated molecules separately from existing
   evidence-backed molecules.

Generated molecules are computational structures and research hypotheses. They
are not known actives, do not have direct experimental evidence unless exact
imported results are linked to the exact tested structure, and are not claimed
to bind targets, modulate targets, treat disease, or be safe.
Experiment-readiness means worth expert triage, not proven activity. Oracle
scores, surrogate estimates, uncertainty values, and medchem critique are
prioritization signals, not evidence. No fake evidence is generated for
generated molecules.

V0.4 adds developability triage after generation and before evidence scoring.
The triage uses physicochemical descriptors, drug-likeness heuristics,
chemistry alerts, rule-based ADMET triage, toxicity-risk flags,
synthetic-accessibility heuristics, synthesizability scoring, chemical liability
flags, and optional structure-aware filter state. These outputs are
computational risk flags only and require medicinal chemistry, toxicology,
pharmacology, synthesis, and domain expert review. ADMET predictions do not
prove safety. Synthesizability scoring does not provide synthesis routes and
does not prove practical synthesizability. V0.4 does not implement
retrosynthesis, synthesis planning, wet-lab prediction, dosage, patient
treatment, or clinical guidance. No synthesis instructions are provided.

Structure retrieval and docking are optional. Structure retrieval is metadata
only unless additional structure-aware filtering is enabled. Docking is disabled
by default, must be explicitly requested, and docking scores do not prove
binding. Docking results are weak computational heuristics, not experimental
evidence.

Existing evidence-backed molecules are not silently removed by default because
disease/target evidence remains separate from developability risk. Generated
molecules may be filtered more aggressively because they have no direct
experimental evidence unless future real evidence is retrieved.

## Literature Evidence Policy

PubMed is the primary literature source. The literature module searches
PubMed, retrieves paper metadata and source-provided abstracts through NCBI
E-utilities, deduplicates papers, extracts citations, and applies conservative
rule-based claim extraction. OpenAlex enrichment is optional and is used for
citation count, open-access, concept/topic, landing-page, and retraction
metadata; it is not the primary biomedical evidence source.

Literature source failures are configurable:

- Default mode: warn and continue when literature retrieval or optional
  enrichment fails and database evidence is sufficient.
- Strict mode: fail the run when a required literature source is unavailable.

Scientific-integrity rules:

- No fake citations, papers, PMIDs, DOIs, or OpenAlex IDs are created.
- No full copyrighted articles are stored.
- Claims are extracted only from title, abstract, metadata, and
  source-provided snippets.
- Mention-only evidence is labeled as mention-only and is not treated as proof.
- Clinical literature is distinguished from preclinical, review, computational,
  in-vitro, animal, case-report, and unknown evidence.
- Safety and contradictory literature can reduce scores or confidence.
- A citation is never described as proving therapeutic efficacy unless the
  extracted claim is `clinical_support` and the queried molecule and disease
  are both present.

## Agent Architecture

The orchestrator runs agents in this order:

1. `DiseaseResolverAgent`
2. `TargetDiscoveryAgent`
3. `MoleculeRetrievalAgent`
4. `LiteratureEvidenceAgent`
5. `NovelMoleculeAgent`
6. `DevelopabilityAssessmentAgent`
7. `EvidenceScoringAgent`
8. `ReportWriterAgent`

Each successful agent appends an `AgentTrace`. Critical data failures stop the
pipeline and prevent a normal success report from being written.

Core schemas are Pydantic models:

- `Disease`
- `EvidenceItem`
- `LiteratureQuery`
- `LiteraturePaper`
- `Citation`
- `EvidenceClaim`
- `LiteratureEvidenceItem`
- `LiteratureEvidenceBundle`
- `Target`
- `MoleculeCandidate`
- `GeneratedMoleculeHypothesis`
- `ScoreBreakdown`
- `AgentTrace`
- `RankingRun`

## Scoring Formula

V0.4 uses a deterministic transparent heuristic over retrieved evidence. Without
supported literature evidence or developability assessment, the base formula is:

```text
final_score =
  0.25 * disease_target_relevance +
  0.20 * molecule_target_evidence +
  0.20 * mechanism_plausibility +
  0.10 * clinical_precedence +
  0.10 * safety_prior +
  0.10 * data_quality +
  0.05 * novelty_or_repurposing_value
```

Every component is bounded between 0 and 1. Components are derived only from
retrieved target scores, molecule evidence, mechanisms, activity potency, assay
metadata, indications, warnings, development status, source diversity,
identifiers, and provenance. When conservative literature claims are present,
they act as bounded modifiers to existing components:

- Disease-target literature may modestly increase disease-target relevance.
- Molecule-target and mechanism literature may modestly increase
  molecule-target evidence and mechanism plausibility.
- Clinical literature can increase clinical precedence only when the queried
  molecule and disease are both present; review articles alone do not count as
  clinical precedence.
- Mention-only literature has minimal effect.
- Safety or contradictory literature can lower safety prior and confidence.
- Retracted records do not improve scores.

When developability assessment is available, V0.4 applies a bounded adjustment
using the heuristic developability score and conservative penalties for
review/high-risk/insufficient-structure flags. This is a computational
triage adjustment, not a safety conclusion.

Scores are prioritization aids, not validated predictions of efficacy or safety.

Generated molecule hypotheses are scored separately by seed similarity,
target-relevance context, and basic RDKit descriptor fit. This score is a
generation-prioritization heuristic only. It is not evidence of disease
activity, target engagement, safety, efficacy, practical synthesizability, or clinical
utility. Generated molecules have no direct experimental evidence attached, and
no fake evidence records are created for them.

Every ranked candidate includes:

- Final score.
- Confidence.
- Component-level score breakdown.
- Human-readable explanation.
- Evidence summaries.
- Literature evidence summaries with citations or explicit absence labels.
- Source provenance.
- Warnings for missing or heuristic evidence dimensions.

## Fail-Fast Behavior

The pipeline stops on:

- Disease resolution failure.
- Target discovery failure.
- Molecule retrieval failure.
- No evidence-backed candidates.
- External API unavailability.
- Missing real retrieved evidence for ranked candidates.

On failure, the CLI prints a clear error and exits with a non-zero status. It
does not write a normal `report.md` that looks successful.

## Known Limitations

- Public databases may be incomplete, stale, unavailable, or rate-limited.
- Source records may use inconsistent identifiers and terminology.
- Scores are heuristic and not experimentally validated.
- No wet-lab validation is performed by this software.
- V2.0 is for internal research use only.
- V2.0 is not a regulated clinical product.
- Codex is an orchestration and summarization layer, not scientific truth.
- No clinical recommendation, diagnosis, prescription, dosage, or treatment
  guidance is provided.
- No synthesis instructions, lab protocols, or experimental execution
  instructions are provided.
- molecule-ranker does not claim that molecules cure, treat, are safe, active,
  effective, binding, or synthesizable.
- Approved status does not imply safety or relevance for the queried disease.
- Absence of evidence is not evidence of absence.
- Literature evidence can be absent for a candidate; absence is labeled rather
  than filled with inferred claims.
- Mention-only literature is not proof of disease relevance, target engagement,
  efficacy, or safety.
- Clinical literature evidence is reported separately from preclinical and
  review evidence.
- Generated molecule hypotheses are in-silico only and have no attached
  invented evidence.
- Generated molecule hypotheses are not known actives and are ranked separately
  from existing molecules by default.
- Experiment-readiness means worth expert triage, not proven activity.
- Surrogate models and oracle scores are prioritization signals, not evidence.
- Developability triage and rule-based ADMET risk flags are computational
  heuristics, not validated safety evidence.
- Docking is disabled by default and docking scores do not prove binding.
- Review workflows support expert triage and handoff, but review decisions are
  not biomedical evidence.
- Review decisions and expert feedback are not biomedical evidence.
- Experimental evidence is disabled unless configured and imported from files.
- Integration writes are disabled by default; dry-run/read-only modes are the
  default.
- Validation handoff packets are high-level planning artifacts and do not
  include lab protocols.
- Enterprise validation packages are software/process validation artifacts, not
  clinical validation, regulatory approval, GxP compliance, benchmark proof, or
  prospective validation proof.

## Roadmap

- V0.1: stronger live biomedical adapters and source normalization.
- V0.2: literature evidence retrieval and citation extraction.
- V0.3: target-conditioned novel molecule generation.
- V0.4: developability, ADMET, toxicity, synthesizability, and optional
  structure-aware filters.
- V0.5: expert review workflow and human-in-the-loop triage.
- V0.6: experimental feedback loop and active learning from assay results.
- V0.7: Codex CLI LLM backbone, project workspace, multi-run management, and
  local API server.
- V0.8: production deployment, authentication, roles, team collaboration, and
  hosted dashboard.
- V0.9: external integrations for ELN/LIMS, compound registry, assay providers,
  and data warehouse.
- V1.0: validated internal research platform MVP.
- V1.1: agentic design optimization and experiment-ready molecule
  prioritization.
- V1.2: stronger predictive model plugin system and calibrated assay-specific
  surrogates.
- V1.3: advanced structure-based design and protein-ligand workflow hardening.
- V1.4: multi-objective portfolio optimization and program-level decision
  analytics.
- V1.5: cross-program knowledge graph and mechanism-level reasoning.
- V1.6: automated hypothesis generation and testable research-question
  planning.
- V1.7: closed-loop campaign planning and budget-aware execution management.
- V1.8: scientific evaluation benchmark suite and prospective validation
  analytics.
- V1.9: usability, performance, reliability, and enterprise pilot hardening.
- V2.0: validated enterprise discovery operating system.
- V2.1: Codex Runtime Agent OS.
- V2.2: Codex tool-use expansion with MCP/plugin ecosystem and workflow
  marketplace.
- V2.3: autonomous multi-agent research operations with supervised delegation.
- V3.0: autonomous discovery operating system with validated human-governed
  agentic workflows.

## Development

CI runs the same default checks on pull requests and pushes to `main`:

```bash
uv sync --all-groups --frozen
uv run ruff check .
uv run pyright
uv run pytest
```

Run normal unit tests:

```bash
uv run pytest
```

Normal unit tests use mocked public-source responses and do not require network
access. Live public API smoke tests live under `tests_live/` and are excluded
from the default pytest test path.

Run live public API smoke tests explicitly:

```bash
uv run pytest -m live tests_live/
```

Live tests are intentionally not deterministic. They depend on current Open
Targets, ChEMBL, PubChem, PubMed, and OpenAlex availability, rate limits,
schemas, and records. They assert structural properties only, not exact
biomedical targets, molecules, PMIDs, citation counts, or scores. The default
GitHub Actions CI does not run live network tests; the workflow includes a
manual `workflow_dispatch` live smoke job for maintainers.

Run lint:

```bash
uv run ruff check .
```

Run type checking:

```bash
uv run pyright
```
