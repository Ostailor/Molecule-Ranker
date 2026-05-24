# Alzheimer Disease Example

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

V0.0 uses public biomedical APIs. If no public records are returned or an API is
unavailable, the command raises a domain-specific error rather than producing
fake candidates. The output is a research hypothesis artifact, not medical
advice.
