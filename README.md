# molecule-ranker

`molecule-ranker` is an agent-first drug discovery research prototype. Given a
disease name, V0.2 resolves the disease through public biomedical data sources,
discovers evidence-backed targets, retrieves existing molecules linked to those
targets, retrieves real literature evidence, and ranks the molecules as
transparent research hypotheses.

The app does not discover cures, does not provide medical advice, and does not
provide dosage or patient treatment instructions. Ranked molecules are candidate
hypotheses for therapeutic relevance and require experimental validation.

## V0.2 Scope

V0.2 implements existing-molecule ranking only:

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
- Write `candidates.json`, `report.md`, and `trace.json`.
- Include a `NovelMoleculeAgent` stub that does not generate molecules.
- Cache real public API responses with source provenance and TTL.
- Provide adapter health checks and opt-in live smoke tests.

V0.2 does not:

- Generate novel molecules.
- Create placeholder molecules.
- Use fixture biomedical data in production.
- Invent fallback targets, molecules, evidence, citations, or scores.
- Use LLMs to invent citations, paper claims, or biomedical relationships.
- Create fake citations or placeholder papers.
- Store full copyrighted articles.
- Claim that a molecule cures a disease.
- Make patient-specific recommendations.

Unit tests use mocked data only to test behavior deterministically. Production
code uses real public biomedical data adapters and fails if required data cannot
be retrieved.

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

Run a ranking job:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Normal V0.2 runs include PubMed literature retrieval by default:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-literature \
  --literature-source pubmed \
  --openalex-enrichment
```

Run without literature evidence when you only want database-derived ranking:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-literature
```

Use strict literature mode when PubMed availability is required for the run:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --strict-literature
```

Write the normal report files and print a JSON summary with literature counts:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --json
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

Important V0.2 configuration options map to typed `RankerConfig` fields:

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

Run live literature smoke tests explicitly:

```bash
uv run pytest -m live tests_live/test_live_literature.py
```

JSON summary output:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10 --json
```

Files are written under:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/trace.json
```

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

HTTP requests are made only inside adapter classes. Tests may mock adapter
responses, but production code does not import test fixtures or ship fixture
biomedical knowledge.

## Literature Evidence Policy

PubMed is the primary V0.2 literature source. The literature module searches
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
5. `NovelMoleculeAgent` stub
6. `EvidenceScoringAgent`
7. `ReportWriterAgent`

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
- `ScoreBreakdown`
- `AgentTrace`
- `RankingRun`

## Scoring Formula

V0.2 uses a deterministic transparent heuristic over retrieved evidence. Without
supported literature evidence, the V0.1 formula is preserved:

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

Scores are prioritization aids, not validated predictions of efficacy or safety.

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
- Novel molecule generation is intentionally not implemented in V0.2.

## Roadmap

- V0.1: stronger live biomedical adapters and source normalization.
- V0.2: literature evidence retrieval and citation extraction.
- V0.3: target-conditioned novel molecule generation.
- V0.4: ADMET and synthesizability filters.
- V0.5: human expert review workflow.

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
