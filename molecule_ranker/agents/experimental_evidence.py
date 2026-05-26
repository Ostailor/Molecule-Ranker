from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.experiments.evidence import assay_result_to_evidence_item
from molecule_ranker.experiments.linking import LinkingConfig, link_assay_results
from molecule_ranker.experiments.schemas import AssayResult, ExperimentalEvidenceSummary
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.experiments.validation import result_quality_score
from molecule_ranker.schemas import MoleculeCandidate


class ExperimentalEvidenceAgent(BaseAgent):
    name = "ExperimentalEvidenceAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_experimental_evidence", False)):
            context.config[self._trace_key] = self._disabled_metadata()
            return context

        store = ExperimentalResultStore(
            Path(
                str(
                    context.config.get(
                        "experimental_db_path",
                        ".review/molecule-ranker-experiments.sqlite",
                    )
                )
            )
        )
        loaded_results = self._source_filtered_results(
            store.list_results(),
            context.config.get("experimental_result_source_filter"),
        )
        linked_results = link_assay_results(
            loaded_results,
            candidates=context.candidates,
            generated_molecules=context.generated_candidates,
            config=LinkingConfig(
                allow_fuzzy_name_matching=not bool(
                    context.config.get("strict_experimental_linking", True)
                )
            ),
        )
        if not bool(context.config.get("include_inconclusive_results", True)):
            linked_results = [
                result for result in linked_results if result.outcome_label != "inconclusive"
            ]

        candidate_groups, generated_groups, unlinked = self._group_linked_results(linked_results)
        context.candidates = [
            self._attach_candidate_evidence(
                candidate,
                candidate_groups.get(candidate.name, []),
                context,
            )
            for candidate in context.candidates
        ]

        candidate_summaries = {
            candidate.name: self._build_summary(candidate, candidate_groups.get(candidate.name, []))
            for candidate in context.candidates
            if candidate_groups.get(candidate.name)
        }
        generated_summaries = {
            name: self._build_generated_summary(name, results)
            for name, results in generated_groups.items()
        }
        payload = {
            "results": [
                result.model_dump(mode="json")
                for result in linked_results
            ],
            "loaded_result_ids": [result.result_id for result in loaded_results],
            "linked_result_ids": [
                result.result_id
                for result in linked_results
                if result.metadata.get("linked_candidate_id")
            ],
            "candidate_summaries": {
                name: summary.model_dump(mode="json")
                for name, summary in candidate_summaries.items()
            },
            "generated_summaries": {
                name: summary.model_dump(mode="json")
                for name, summary in generated_summaries.items()
            },
            "unlinked_result_ids": [result.result_id for result in unlinked],
            "limitations": [
                (
                    "Experimental results are direct evidence only for the tested molecule "
                    "and assay context."
                ),
                "In-vitro or biochemical results do not imply clinical efficacy.",
                "No experimental result is inferred from model scores.",
            ],
        }
        context.config["experimental_evidence"] = payload
        context.config[self._trace_key] = self._trace_metadata(
            loaded_results=loaded_results,
            linked_results=linked_results,
            unlinked_results=unlinked,
            candidate_groups=candidate_groups,
            generated_groups=generated_groups,
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        metadata = context.config.get(self._trace_key, {})
        if not metadata.get("enabled", False):
            return "Experimental evidence disabled; skipped imported assay results."
        return (
            f"Loaded {metadata.get('results_loaded', 0)} experimental result(s), "
            f"linked {metadata.get('results_linked', 0)}."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return dict(context.config.get(self._trace_key, self._disabled_metadata()))

    @property
    def _trace_key(self) -> str:
        return "ExperimentalEvidenceAgent.trace_metadata"

    def _source_filtered_results(
        self,
        results: list[AssayResult],
        source_filter: object,
    ) -> list[AssayResult]:
        if source_filter in (None, "", []):
            return results
        allowed = (
            {str(item) for item in source_filter}
            if isinstance(source_filter, list)
            else {str(source_filter)}
        )
        return [result for result in results if result.source in allowed]

    def _group_linked_results(
        self,
        results: list[AssayResult],
    ) -> tuple[dict[str, list[AssayResult]], dict[str, list[AssayResult]], list[AssayResult]]:
        by_candidate: dict[str, list[AssayResult]] = defaultdict(list)
        by_generated: dict[str, list[AssayResult]] = defaultdict(list)
        unlinked: list[AssayResult] = []
        for result in results:
            metadata = result.metadata
            linked_id = metadata.get("linked_candidate_id")
            if not linked_id:
                unlinked.append(result)
                continue
            if metadata.get("linked_generated_id"):
                by_generated[str(linked_id)].append(result)
            else:
                by_candidate[result.candidate_name].append(result)
        return dict(by_candidate), dict(by_generated), unlinked

    def _attach_candidate_evidence(
        self,
        candidate: MoleculeCandidate,
        results: list[AssayResult],
        context: PipelineContext,
    ) -> MoleculeCandidate:
        if candidate.origin == "generated":
            return candidate
        evidence = list(candidate.evidence)
        warnings = list(candidate.warnings)
        for result in results:
            if not self._should_attach_evidence(result, context):
                if result.outcome_label == "failed_qc":
                    warnings.append("failed_qc results are recorded but not score-promoting")
                if result.outcome_label == "inconclusive":
                    warnings.append(
                        "inconclusive experimental results are recorded but not score-promoting"
                    )
                continue
            evidence.append(assay_result_to_evidence_item(result))
            if result.outcome_label == "negative" or result.activity_direction in {
                "toxic",
                "worsened",
            }:
                warnings.append("Imported experimental result may lower prioritization.")
        return candidate.model_copy(
            update={
                "evidence": evidence,
                "warnings": sorted(set(warnings)),
            }
        )

    def _should_attach_evidence(self, result: AssayResult, context: PipelineContext) -> bool:
        if result.outcome_label in {"failed_qc", "inconclusive", "not_tested", "invalid"}:
            return False
        if (
            bool(context.config.get("require_qc_passed_for_score", True))
            and result.qc_status != "passed"
        ):
            return False
        return bool(result.metadata.get("linked_candidate_id"))

    def _build_summary(
        self,
        candidate: MoleculeCandidate,
        results: list[AssayResult],
    ) -> ExperimentalEvidenceSummary:
        counts = Counter(result.outcome_label for result in results)
        endpoint_summaries = self._endpoint_summaries(results)
        return ExperimentalEvidenceSummary(
            candidate_id=self._candidate_id(candidate),
            candidate_name=candidate.name,
            candidate_origin=candidate.origin,
            result_count=len(results),
            positive_count=counts.get("positive", 0),
            negative_count=counts.get("negative", 0),
            inconclusive_count=counts.get("inconclusive", 0),
            failed_qc_count=counts.get("failed_qc", 0),
            endpoint_summaries=endpoint_summaries,
            best_supporting_results=[
                result.result_id for result in results if result.outcome_label == "positive"
            ],
            key_negative_results=[
                result.result_id for result in results if result.outcome_label == "negative"
            ],
            safety_concerns=[
                result.result_id for result in results if result.activity_direction == "toxic"
            ],
            confidence=self._average_quality(results),
            interpretation=self._interpret_results(counts),
            warnings=self._summary_warnings(counts),
            metadata={
                "linked_result_ids": [result.result_id for result in results],
                "direct_evidence_result_ids": [
                    result.result_id for result in results if self._is_direct_score_evidence(result)
                ],
            },
        )

    def _build_generated_summary(
        self,
        generated_name: str,
        results: list[AssayResult],
    ) -> ExperimentalEvidenceSummary:
        counts = Counter(result.outcome_label for result in results)
        return ExperimentalEvidenceSummary(
            candidate_id=generated_name,
            candidate_name=generated_name,
            candidate_origin="generated",
            result_count=len(results),
            positive_count=counts.get("positive", 0),
            negative_count=counts.get("negative", 0),
            inconclusive_count=counts.get("inconclusive", 0),
            failed_qc_count=counts.get("failed_qc", 0),
            endpoint_summaries=self._endpoint_summaries(results),
            best_supporting_results=[
                result.result_id for result in results if result.outcome_label == "positive"
            ],
            key_negative_results=[
                result.result_id for result in results if result.outcome_label == "negative"
            ],
            safety_concerns=[
                result.result_id for result in results if result.activity_direction == "toxic"
            ],
            confidence=self._average_quality(results),
            interpretation=(
                "Generated molecule has direct imported experimental evidence only for "
                "the explicitly linked result(s); no seed or analog generalization is made."
            ),
            warnings=self._summary_warnings(counts),
            metadata={
                "linked_result_ids": [result.result_id for result in results],
                "direct_evidence_result_ids": [result.result_id for result in results],
            },
        )

    def _endpoint_summaries(self, results: list[AssayResult]) -> dict[str, dict[str, Any]]:
        summaries: dict[str, dict[str, Any]] = {}
        for result in results:
            endpoint = result.assay_context.endpoint.name
            summary = summaries.setdefault(endpoint, {"result_count": 0, "outcome_counts": {}})
            summary["result_count"] += 1
            summary["outcome_counts"][result.outcome_label] = (
                summary["outcome_counts"].get(result.outcome_label, 0) + 1
            )
        return summaries

    def _trace_metadata(
        self,
        *,
        loaded_results: list[AssayResult],
        linked_results: list[AssayResult],
        unlinked_results: list[AssayResult],
        candidate_groups: dict[str, list[AssayResult]],
        generated_groups: dict[str, list[AssayResult]],
    ) -> dict[str, Any]:
        counts = Counter(result.outcome_label for result in linked_results)
        warnings = sorted(
            {
                str(result.metadata.get("ambiguity_warning"))
                for result in linked_results
                if result.metadata.get("ambiguity_warning")
            }
        )
        return {
            "enabled": True,
            "results_loaded": len(loaded_results),
            "results_linked": len(linked_results) - len(unlinked_results),
            "results_unlinked": len(unlinked_results),
            "candidates_with_results": len(candidate_groups),
            "generated_molecules_with_results": len(generated_groups),
            "positive_count": counts.get("positive", 0),
            "negative_count": counts.get("negative", 0),
            "inconclusive_count": counts.get("inconclusive", 0),
            "failed_qc_count": counts.get("failed_qc", 0),
            "warnings": warnings,
        }

    def _disabled_metadata(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "results_loaded": 0,
            "results_linked": 0,
            "results_unlinked": 0,
            "candidates_with_results": 0,
            "generated_molecules_with_results": 0,
            "positive_count": 0,
            "negative_count": 0,
            "inconclusive_count": 0,
            "failed_qc_count": 0,
            "warnings": [],
        }

    def _candidate_id(self, candidate: MoleculeCandidate) -> str | None:
        if candidate.identifiers:
            for key in ["chembl", "pubchem", "drugbank", "id"]:
                if key in candidate.identifiers:
                    return candidate.identifiers[key]
            return next(iter(candidate.identifiers.values()))
        return None

    def _average_quality(self, results: list[AssayResult]) -> float:
        if not results:
            return 0.0
        return round(sum(result_quality_score(result) for result in results) / len(results), 3)

    def _is_direct_score_evidence(self, result: AssayResult) -> bool:
        return result.outcome_label not in {"failed_qc", "inconclusive", "not_tested", "invalid"}

    def _interpret_results(self, counts: Counter[str]) -> str:
        if counts.get("positive", 0) and counts.get("negative", 0):
            return "Mixed imported experimental outcomes; no clinical efficacy claim is made."
        if counts.get("positive", 0):
            return "Imported assay results include positive evidence for the tested context."
        if counts.get("negative", 0):
            return "Imported assay results include negative evidence for the tested context."
        return "Imported assay results are inconclusive, failed QC, or not score-promoting."

    def _summary_warnings(self, counts: Counter[str]) -> list[str]:
        warnings: list[str] = []
        if counts.get("failed_qc", 0):
            warnings.append("failed_qc results are recorded but not score-promoting")
        if counts.get("inconclusive", 0):
            warnings.append("inconclusive results are recorded but not score-promoting")
        return warnings
