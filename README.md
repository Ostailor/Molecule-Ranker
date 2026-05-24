# molecule-ranker

`molecule-ranker` is an agent-first drug discovery research prototype. Given a
disease name, V0.0 resolves the disease through public biomedical data sources,
discovers evidence-backed targets, retrieves existing molecules linked to those
targets, and ranks the molecules as transparent research hypotheses.

The app does not discover cures, does not provide medical advice, and does not
provide dosage or patient treatment instructions. Ranked molecules are candidate
hypotheses for therapeutic relevance and require experimental validation.

## V0.0 Scope

V0.0 implements existing-molecule ranking only:

- Resolve disease names to public biomedical disease entities.
- Retrieve real disease-associated targets.
- Retrieve existing molecules associated with those targets.
- Score candidates with a transparent component breakdown.
- Write `candidates.json`, `report.md`, and `trace.json`.
- Include a `NovelMoleculeAgent` stub that does not generate molecules.

V0.0 does not:

- Generate novel molecules.
- Create placeholder molecules.
- Use fixture biomedical data in production.
- Invent fallback targets, molecules, evidence, citations, or scores.
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

Useful options:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --max-targets 25 \
  --max-molecules-per-target 10 \
  --verbose
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
- ChEMBL: target-linked existing molecules, mechanisms, and development status
  where available.
- PubChem: molecule identifier and chemical metadata enrichment where available.

HTTP requests are made only inside adapter classes. Tests may mock adapter
responses, but production code does not import test fixtures or ship fixture
biomedical knowledge.

## Agent Architecture

The orchestrator runs agents in this order:

1. `DiseaseResolverAgent`
2. `TargetDiscoveryAgent`
3. `MoleculeRetrievalAgent`
4. `NovelMoleculeAgent` stub
5. `EvidenceScoringAgent`
6. `ReportWriterAgent`

Each successful agent appends an `AgentTrace`. Critical data failures stop the
pipeline and prevent a normal success report from being written.

Core schemas are Pydantic models:

- `Disease`
- `EvidenceItem`
- `Target`
- `MoleculeCandidate`
- `ScoreBreakdown`
- `AgentTrace`
- `RankingRun`

## Scoring Formula

V0.0 uses a deterministic transparent heuristic over retrieved evidence:

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
retrieved target scores, molecule evidence, mechanisms, development status,
source diversity, identifiers, and provenance. Scores are prioritization aids,
not validated predictions of efficacy or safety.

Every ranked candidate includes:

- Final score.
- Confidence.
- Component-level score breakdown.
- Human-readable explanation.
- Evidence summaries.
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
- Novel molecule generation is intentionally not implemented in V0.0.

## Roadmap

- V0.1: stronger live biomedical adapters and source normalization.
- V0.2: literature evidence retrieval and citation extraction.
- V0.3: target-conditioned novel molecule generation.
- V0.4: ADMET and synthesizability filters.
- V0.5: human expert review workflow.

## Development

Run tests:

```bash
uv run pytest
```

Run lint:

```bash
uv run ruff check .
```

Run type checking:

```bash
uv run pyright
```
