# Validation Package

The V2.0 validation evidence package collects deterministic software and
process evidence for enterprise release readiness.

## Generate

```bash
molecule-ranker validate v2-package --output validation_package/
molecule-ranker validate v2-package --zip validation_package.zip
```

## Included Evidence

- Release manifest.
- Version and git commit.
- Dependency lock hash.
- Artifact and API contract validation.
- Golden workflow results.
- Guardrail benchmark report.
- Security audit report.
- Performance profile.
- Readiness report.
- Backup/restore verification.
- Migration dry-run report.
- Support bundle validation.
- Deployment smoke test.
- Codex guardrail evaluation.
- External integration dry-run validation.
- Prospective validation demo.
- Known limitations.

## Exclusions

Packages exclude secrets, cache, full copyrighted text, raw unapproved data, and
Codex transcripts by default. This is software/platform validation evidence,
not regulatory approval, not GxP compliance unless separately assessed, and not
clinical validation.
