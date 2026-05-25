from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.data_sources.base import TargetDiscoveryDataSource
from molecule_ranker.data_sources.errors import TargetDiscoveryError
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.schemas import Target


class TargetDiscoveryAgent(BaseAgent):
    name = "TargetDiscoveryAgent"

    def __init__(self, data_source: TargetDiscoveryDataSource | None = None) -> None:
        super().__init__()
        self._data_source = data_source or OpenTargetsAdapter()

    def process(self, context: PipelineContext) -> PipelineContext:
        if context.disease is None:
            raise TargetDiscoveryError("Target discovery requires a resolved disease.")
        target_limit = int(
            context.config.get(
                "default_target_limit",
                context.config.get("target_limit", 20),
            )
        )
        source_limit = int(context.config.get("target_source_limit", max(target_limit * 5, 100)))
        targets = self._data_source.discover_targets(context.disease, limit=source_limit)
        context.config[f"{self.name}.retrieved_count"] = len(targets)
        evidence_backed = [target for target in targets if self._has_real_evidence(target)]
        rejected = len(targets) - len(evidence_backed)
        context.config[f"{self.name}.rejected_without_evidence"] = rejected
        context.config[f"{self.name}.evidence_backed_count"] = len(evidence_backed)
        context.config[f"{self.name}.metadata_fields_enriched"] = (
            self._metadata_fields_enriched(evidence_backed)
        )
        if not evidence_backed:
            raise TargetDiscoveryError(
                f"No evidence-backed targets found for {context.disease.canonical_name}."
            )
        sorted_targets = sorted(
            evidence_backed,
            key=lambda target: target.disease_relevance_score,
            reverse=True,
        )
        context.targets = sorted_targets[:target_limit]
        context.config[f"{self.name}.summary"] = (
            f"Retrieved {len(targets)} targets and retained {len(context.targets)}."
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return str(context.config.get(f"{self.name}.summary", "Discovered targets."))

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        disease_id = None
        if context.disease is not None:
            disease_id = (
                context.disease.identifiers.get("open_targets")
                or context.disease.identifiers.get("efo")
                or context.disease.identifiers.get("mondo")
            )
        source_name = getattr(
            self._data_source,
            "source_name",
            self._data_source.__class__.__name__,
        )
        metadata = {
            "disease_id": disease_id,
            "source": source_name,
            "targets_retrieved": context.config.get(f"{self.name}.retrieved_count", 0),
            "raw_target_count": context.config.get(f"{self.name}.retrieved_count", 0),
            "evidence_backed_target_count": context.config.get(
                f"{self.name}.evidence_backed_count", len(context.targets)
            ),
            "targets_retained": len(context.targets),
            "top_target_symbols": [target.symbol for target in context.targets[:10]],
            "rejected_without_evidence": context.config.get(
                f"{self.name}.rejected_without_evidence", 0
            ),
            "rejected_target_count": context.config.get(
                f"{self.name}.rejected_without_evidence", 0
            ),
            "metadata_fields_enriched": context.config.get(
                f"{self.name}.metadata_fields_enriched", []
            ),
        }
        source_trace = getattr(self._data_source, "last_trace_metadata", {})
        if isinstance(source_trace, dict):
            for key in (
                "pages_fetched",
                "records_fetched",
                "records_retained",
                "truncated",
                "retry_count",
                "rate_limit_retry_count",
            ):
                metadata[key] = source_trace.get(key)
        return metadata

    def _has_real_evidence(self, target: Target) -> bool:
        return any(item.source and item.source_record_id for item in target.evidence)

    def _metadata_fields_enriched(self, targets: list[Target]) -> list[str]:
        fields: set[str] = set()
        for target in targets:
            if target.identifiers:
                fields.add("identifiers")
            if target.target_class:
                fields.add("target_class")
            if target.tractability:
                fields.add("tractability")
            if target.safety:
                fields.add("safety")
            for key, value in target.metadata.items():
                if value not in (None, "", [], {}):
                    fields.add(str(key))
        return sorted(fields)
