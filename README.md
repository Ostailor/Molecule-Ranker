# molecule-ranker

`molecule-ranker` is an agent-first drug discovery research prototype. Given a
disease name, V0.5 resolves the disease through public biomedical data sources,
discovers evidence-backed targets, retrieves existing molecules linked to those
targets, retrieves real literature evidence, ranks the molecules as transparent
research hypotheses, and can optionally generate target-conditioned in-silico
molecule hypotheses from those retrieved structures. V0.4 added computational
developability triage for existing and generated molecules. V0.5 adds a local
expert review workspace for human-in-the-loop triage, dossiers, follow-up
requests, validation handoffs, feedback ingestion, and audit trails.

The app does not discover cures, does not claim generated molecules treat or are
active against a disease, does not provide medical advice, and does not provide
dosage or patient treatment instructions. Ranked molecules and generated
structures are research hypotheses that require independent validation.
Generated molecules are computational hypotheses only: they are not known
actives, have no direct experimental evidence, and are ranked separately from
existing evidence-backed molecules unless explicitly requested otherwise.

## V0.4 Scope

V0.4 implements existing-molecule ranking, opt-in generated hypotheses, and
developability-aware computational triage:

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
- Use SELFIES mutation/crossover as the first generation backend over real
  retrieved seed molecules.
- Use RDKit for generated-structure validation, canonicalization, descriptors,
  fingerprints, similarity, and coarse chemistry filters.
- Assess physicochemical descriptors, drug-likeness heuristics, chemistry
  alerts, rule-based ADMET triage, toxicity-risk flags, synthetic-accessibility
  heuristics, synthesizability scoring, and chemical liability flags for
  existing and generated molecules when parseable structures are available.
- Apply a bounded developability adjustment to evidence-backed ranking scores.
- Optionally retrieve target structure metadata and optionally run docking only
  when explicitly enabled. Docking is disabled by default.
- Record optional structure-aware filter pass/fail state without claiming that a
  molecule is safe, binds a target, or is practically synthesizable.
- Rank generated structures separately from evidence-backed molecules.
- Write `candidates.json`, `generated_candidates.json`,
  `generation_trace.json`, `developability.json`,
  `developability_assessments.json`, `developability_report.md`, `report.md`,
  and `trace.json`.
- Cache real public API responses with source provenance and TTL.
- Provide adapter health checks and opt-in live smoke tests.

V0.4 does not:

- Create placeholder molecules.
- Use fixture biomedical data in production.
- Use hardcoded generated molecules.
- Invent fallback targets, molecules, evidence, citations, or scores.
- Invent evidence for generated molecules.
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

Unit tests use mocked data only to test behavior deterministically. Production
code uses real public biomedical data adapters and fails if required data cannot
be retrieved.

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

Normal V0.4 ranking includes PubMed literature retrieval and developability
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

Benchmark a generated-molecule artifact with internal V0.3 quality metrics:

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
  --generation-method selfies_mutation \
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

Important V0.5 configuration options map to typed `RankerConfig` fields:

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
- `generation_method`: generated molecule backend; V0.3 supports
  `selfies_mutation`.
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

The generation pipeline:

1. Selects real retrieved existing molecules as seeds.
2. Builds generation objectives for evidence-backed targets with selected seeds.
3. Uses SELFIES mutation, insertion, deletion, and seed-seed crossover as the
   first backend.
4. Decodes generated SELFIES into structures and validates them with RDKit.
5. Canonicalizes SMILES, computes InChIKey when possible, descriptors,
   fingerprints, and Tanimoto similarity.
6. Filters invalid, duplicate, near-duplicate, distant, and chemically
   unreasonable structures using coarse generation rules.
7. Scores retained generated molecules separately from existing
   evidence-backed molecules.

Generated molecules are computational structures and research hypotheses. They
are not known actives, do not have direct experimental evidence, and are not
claimed to bind targets, modulate targets, treat disease, or be safe. Their
scores are generation-prioritization scores based on seed and target context,
not efficacy predictions. No fake evidence is generated for them.

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

## Limitations

- Public databases may be incomplete, stale, unavailable, or rate-limited.
- Source records may use inconsistent identifiers and terminology.
- Scores are heuristic and not experimentally validated.
- No wet-lab validation is performed by this software.
- No clinical recommendation, diagnosis, prescription, dosage, or treatment
  guidance is provided.
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
- V0.4 implements heuristic developability triage and rule-based ADMET risk
  flags, not validated ADMET prediction, default docking, retrosynthesis,
  synthesis planning, or wet-lab prediction.
- V0.5 review workspaces are local SQLite artifacts and static files, not a
  multi-user production system.
- Review decisions and expert feedback are not biomedical evidence.
- Validation handoff packets are high-level planning artifacts and do not
  include lab protocols.
- No synthesis instructions are provided.

## Roadmap

- V0.1: stronger live biomedical adapters and source normalization.
- V0.2: literature evidence retrieval and citation extraction.
- V0.3: target-conditioned novel molecule generation.
- V0.4: developability, ADMET, toxicity, synthesizability, and optional
  structure-aware filters.
- V0.5: expert review workflow and human-in-the-loop triage.
- V0.6: experimental feedback loop and active learning from assay results.

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
