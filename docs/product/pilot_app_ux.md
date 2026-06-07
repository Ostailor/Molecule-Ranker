# Release V1.0 Pilot App UX

Release V1.0 keeps the app simple. The main user loop is:

Project -> New discovery run -> Result bundle -> Inspect candidates -> Export/save.

The pilot app must not expose raw AgentGraph internals, governance internals,
repair internals, the tool marketplace, MCP internals, or deep policy settings.
These remain internal/admin surfaces.

## 1. Landing Page

Purpose: Explain Molecule Ranker as a research hypothesis generation and
evidence-ranking platform.

Primary user action: Sign up, log in, or request pilot access.

Data shown: Product positioning, pilot access status, high-level workflow, and
research-use boundary.

Disclaimers shown: Research use only; not medical advice; not clinical decision
support; not a regulated medical product.

Hidden internal complexity: Dev V3.0 engine details, Codex runtime, governance
controls, internal tool routing, and policy engine internals.

Error states: Pilot signup unavailable, login provider unavailable, invite token
invalid.

Empty states: No pilot access yet; show request-access path and contact option.

## 2. Login/Signup

Purpose: Authenticate pilot users and collect required acknowledgements.

Primary user action: Log in, create account, or accept an invitation.

Data shown: Email, organization, invite status, required disclaimer
acknowledgement, and account terms placeholder.

Disclaimers shown: Research use only; no patient treatment guidance; no dosing;
no lab protocols; no synthesis instructions.

Hidden internal complexity: Auth provider configuration, tenant resolution,
session internals, and role mapping.

Error states: Invalid credentials, expired invite, suspended account, missing
acknowledgement, organization not found.

Empty states: No organization assigned; show support/contact path.

## 3. Onboarding

Purpose: Guide a pilot user from account creation to first project.

Primary user action: Confirm research use, choose organization, and create the
first project.

Data shown: Onboarding checklist, usage limits, enabled feature summary, and
pilot support contact.

Disclaimers shown: Research use only; evidence/provenance must be independently
reviewed; users are responsible for laws, institutional policies, and lab safety.

Hidden internal complexity: Feature flag evaluation, usage-limit enforcement,
tenant setup, and support workflow internals.

Error states: Incomplete profile, missing organization, disabled project
creation, usage-limit lookup failure.

Empty states: No projects yet; show create-project call to action.

## 4. Project Dashboard

Purpose: Show a user's pilot projects and recent discovery activity.

Primary user action: Open a project or create a new project.

Data shown: Project list, recent runs, run statuses, saved candidates, usage
summary, and last updated timestamps.

Disclaimers shown: Research use only and generated outputs require independent
review.

Hidden internal complexity: Job queue internals, AgentGraph internals, governance
decision internals, and repair history.

Error states: Project load failure, permission denied, usage service unavailable,
run status unavailable.

Empty states: No projects, no runs, or no saved candidates; show the next simple
action.

## 5. Create Project

Purpose: Capture the research context needed to start source-backed ranking.

Primary user action: Create a project from a disease, target area, or project
goal.

Data shown: Project name, goal, disease/area, optional notes, organization, and
usage impact.

Disclaimers shown: Not medical advice; no patient treatment guidance; no lab
protocols; no synthesis instructions.

Hidden internal complexity: Disease resolver internals, prompt construction,
source selection, and policy checks.

Error states: Missing required fields, unsupported input, duplicate project,
project limit reached, unsafe or disallowed request.

Empty states: Blank form with examples that avoid clinical and procedural claims.

## 6. New Discovery Run

Purpose: Start a bounded discovery workflow for a project.

Primary user action: Configure and start a new run.

Data shown: Project goal, workflow mode, enabled data sources, generated
hypotheses toggle if enabled, usage estimate, and required acknowledgement.

Disclaimers shown: Generated molecules and antibodies are computational
hypotheses; no claims of cure, safety, efficacy, activity, binding,
manufacturability, or developability.

Hidden internal complexity: Full workflow graph, raw tool selection, MCP server
details, Codex task planning, and policy engine internals.

Error states: Run limit reached, feature disabled, missing acknowledgement,
source unavailable, unsafe request, queue unavailable.

Empty states: No prior runs; show recommended default workflow.

## 7. Run Progress

Purpose: Let users track run status without exposing internal orchestration.

Primary user action: Watch progress, cancel if supported, or return later.

Data shown: Queued/running/completed/failed status, coarse workflow steps,
timestamps, estimated progress if available, and safe error messages.

Disclaimers shown: Outputs are research artifacts and require human review.

Hidden internal complexity: Raw AgentGraph state, subagent transcripts, repair
loops, governance internals, tool logs, and MCP calls.

Error states: Queue failure, timeout, data source failure, guardrail block,
cancelled run, partial result.

Empty states: Run not started yet or status pending; show refresh/return-later
state.

## 8. Result Bundle Overview

Purpose: Present the completed run as a reviewable research artifact.

Primary user action: Open candidate rankings, evidence/provenance, generated
hypotheses, or export.

Data shown: Project summary, run metadata, ranked-candidate summary, evidence
coverage, limitations, guardrail notices, and export availability.

Disclaimers shown: Result bundles are research artifacts, not clinical
validation, lab instructions, or autonomous scientific truth.

Hidden internal complexity: Internal artifact registry, scoring implementation,
governance matrix, raw logs, and repair traces.

Error states: Missing artifact, partial bundle, export unavailable, permission
denied, stale result.

Empty states: Run completed without results; show explanation and next steps.

## 9. Candidate Ranking Table

Purpose: Compare ranked candidates in a compact, sortable table.

Primary user action: Sort/filter candidates and open candidate detail.

Data shown: Candidate name/identifier, rank, evidence score, provenance count,
notes/favorite status, and flags or limitations.

Disclaimers shown: Rankings are research prioritization aids only and make no
claims of safety, efficacy, activity, binding, manufacturability, or
developability.

Hidden internal complexity: Raw scoring formulas, internal model diagnostics,
agent debates, and governance internals.

Error states: Ranking unavailable, candidate hidden by policy, score unavailable,
filter returns no results.

Empty states: No ranked candidates; show reason and link back to result overview.

## 10. Candidate Detail

Purpose: Show a focused candidate review page for a single candidate.

Primary user action: Review evidence, save/favorite, add notes, or compare.

Data shown: Candidate identifiers, rank, score breakdown summary, evidence links,
provenance, generated hypotheses references, notes, and saved status.

Disclaimers shown: Candidate detail is for research review only and does not
claim cure, safety, efficacy, activity, binding, manufacturability, or
developability.

Hidden internal complexity: Raw model internals, unredacted prompts, full
subagent traces, and repair internals.

Error states: Candidate not found, source unavailable, permission denied,
evidence load failure.

Empty states: No notes, no saved state, or limited evidence; show clear
explanation.

## 11. Evidence/Provenance Viewer

Purpose: Let users inspect source-backed evidence behind rankings and summaries.

Primary user action: Review sources and provenance trail.

Data shown: Source title, citation metadata, source type, extracted claim
summary, provenance links, confidence/limitations, and related candidates.

Disclaimers shown: Evidence and provenance should be independently reviewed.

Hidden internal complexity: Raw retrieval traces, internal extraction prompts,
source adapter internals, and graph construction internals.

Error states: Source unavailable, citation missing, provenance incomplete,
network/source failure, extraction blocked.

Empty states: No evidence available for a filter or candidate; explain that lack
of evidence is not evidence of absence.

## 12. Generated Hypotheses Viewer

Purpose: Show bounded generated hypotheses when the feature is enabled.

Primary user action: Review, save, or add notes to generated hypotheses.

Data shown: Hypothesis text, linked candidates, supporting evidence references,
limitations, generation timestamp, and review status.

Disclaimers shown: Generated hypotheses, molecules, and antibodies are
computational hypotheses only; no claims of cure, safety, efficacy, activity,
binding, manufacturability, or developability.

Hidden internal complexity: Generation prompts, raw model outputs, internal
agent traces, and antibody-generation advanced settings.

Error states: Feature disabled, generation limit reached, hypothesis blocked by
guardrails, incomplete evidence links.

Empty states: No generated hypotheses for this run or feature disabled; show
candidate/evidence review path instead.

## 13. Saved Candidates / Notes

Purpose: Help users collect reviewed candidates and project notes.

Primary user action: Review saved candidates, edit notes, or return to candidate
detail.

Data shown: Saved candidates, note snippets, author, timestamp, related project,
related run, and tags if available.

Disclaimers shown: Notes are user annotations and are not evidence, assay
results, or clinical guidance.

Hidden internal complexity: Audit log internals, collaboration permissions, and
internal review queues.

Error states: Save failed, note update conflict, permission denied, candidate no
longer available.

Empty states: No saved candidates or notes; show how to save from candidate
detail.

## 14. Export Page

Purpose: Let users export result bundles with required disclaimers.

Primary user action: Export Markdown, JSON, or PDF if PDF exists.

Data shown: Export formats, artifact contents, disclaimer acknowledgement, usage
impact, and last export timestamp.

Disclaimers shown: Exports are research artifacts for human review; no medical
advice, clinical decision support, patient treatment guidance, dosing, lab
protocols, or synthesis instructions.

Hidden internal complexity: Storage internals, artifact registry internals,
export renderer internals, and raw logs.

Error states: Export limit reached, PDF unavailable, artifact missing, storage
failure, missing acknowledgement.

Empty states: No exportable result bundle; show run/results path.

## 15. Usage/Billing Page

Purpose: Show usage limits and billing/subscription placeholder state.

Primary user action: Review usage and subscription status.

Data shown: Plan, project count, runs used, Codex tasks used, generated
hypotheses usage, exports used, storage usage, and billing placeholder.

Disclaimers shown: Billing status does not change scientific guardrails or
research-use limitations.

Hidden internal complexity: Stripe internals before billing is enabled, raw
usage-metering implementation, and admin-only plan overrides.

Error states: Usage load failure, billing placeholder unavailable, plan unknown,
limit calculation unavailable.

Empty states: No usage yet; show initial limits and first project action.

## 16. Account Settings

Purpose: Let users manage profile and basic account preferences.

Primary user action: Update profile, organization context, password/session
settings, or notification preferences if available.

Data shown: Name, email, organization, role, plan, acknowledgement status, and
support contact.

Disclaimers shown: Research-use acknowledgement status and legal/policy links.

Hidden internal complexity: Auth provider internals, role mapping internals,
tenant isolation logic, and admin audit internals.

Error states: Profile update failed, email conflict, session expired, permission
denied.

Empty states: Missing optional profile fields; show simple prompts.

## 17. Admin Pilot Dashboard

Purpose: Give operators a compact view of pilot health without exposing deep
internals to normal users.

Primary user action: Review organizations, users, runs, usage, feature flags,
support items, and safety status.

Data shown: Pilot orgs, users, run status summaries, usage summaries, feature
flag status, recent errors, support feedback, and guardrail status summaries.

Disclaimers shown: Admin actions must preserve research-use and scientific
guardrails.

Hidden internal complexity: Raw governance dashboard, kill switches, red-team
suite, repair internals, MCP internals, tool marketplace, and deep policy
settings unless explicitly routed to internal tooling.

Error states: Admin permission denied, partial metrics, unavailable logs, failed
feature-flag update, stale status.

Empty states: No pilot organizations, no runs, or no support feedback yet.

## 18. Feedback/Contact Page

Purpose: Capture pilot feedback and support requests without treating feedback as
scientific evidence.

Primary user action: Submit feedback or contact support.

Data shown: Feedback form, category, related project/run if selected, support
contact, and submission status.

Disclaimers shown: Feedback is not evidence, assay data, clinical guidance, or
scientific validation.

Hidden internal complexity: Support routing, admin queue internals, incident
triage internals, and raw operational logs.

Error states: Submission failed, attachment rejected, related project not found,
support unavailable.

Empty states: No prior feedback; show a simple form.
