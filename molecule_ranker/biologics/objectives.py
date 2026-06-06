from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from molecule_ranker.biologics.schemas import (
    AntibodySequence,
    AntigenContext,
    BiologicType,
)

AntibodyDesignMode = Literal[
    "existing_antibody_ranking",
    "cdr_mutation",
    "sequence_inpainting_plugin",
    "inverse_folding_plugin",
    "epitope_context_design",
    "broad_target_context_design",
]

DEFAULT_REVIEW_REQUIREMENTS = [
    "deterministic_sequence_validation",
    "antibody_numbering_and_cdr_annotation",
    "novelty_check",
    "developability_triage",
    "expert_review_gate",
    "result_bundle_lineage",
]


class AntibodyDesignObjective(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    objective_id: str = Field(
        validation_alias=AliasChoices("objective_id", "design_objective_id")
    )
    disease_name: str | None = None
    target_symbol: str = "UNKNOWN"
    antigen_context_id: str | None = None
    biologic_type: BiologicType = "monoclonal_antibody"
    design_mode: AntibodyDesignMode = Field(
        default="existing_antibody_ranking",
        validation_alias=AliasChoices("design_mode", "mode"),
    )
    seed_sequence_ids: list[str] = Field(default_factory=list)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_constraints: dict[str, Any] = Field(default_factory=dict)
    forbidden_motifs: list[str] = Field(default_factory=list)
    review_requirements: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def apply_required_review_gates(self) -> Self:
        self.review_requirements = _append_unique(
            self.review_requirements,
            DEFAULT_REVIEW_REQUIREMENTS,
        )
        self.hard_constraints.setdefault("generated_hypotheses_only", True)
        self.hard_constraints.setdefault("no_binding_activity_claims", True)
        self.hard_constraints.setdefault("direct_experimental_evidence_required", True)
        self.hard_constraints.setdefault("deterministic_validation_required", True)
        self.hard_constraints.setdefault("novelty_check_required", True)
        self.hard_constraints.setdefault("developability_triage_required", True)
        self.hard_constraints.setdefault("review_gate_required", True)
        return self

    @property
    def design_objective_id(self) -> str:
        return self.objective_id

    @property
    def mode(self) -> str:
        return self.design_mode

    @property
    def target_symbols(self) -> list[str]:
        extra = self.__pydantic_extra__ or {}
        extra_symbols = extra.get("target_symbols")
        if isinstance(extra_symbols, list):
            return [str(symbol) for symbol in extra_symbols]
        return [] if self.target_symbol == "UNKNOWN" else [self.target_symbol]

    @property
    def parent_sequence_ids(self) -> list[str]:
        return self.seed_sequence_ids

    @property
    def max_outputs(self) -> int:
        extra = self.__pydantic_extra__ or {}
        try:
            return max(0, min(int(extra.get("max_outputs", 1)), 25))
        except (TypeError, ValueError):
            return 1

    @property
    def biologic_id(self) -> str:
        extra = self.__pydantic_extra__ or {}
        biologic_id = extra.get("biologic_id") or self.metadata.get("biologic_id")
        if biologic_id:
            return str(biologic_id)
        return f"bio-{self.objective_id}"


def build_antibody_design_objective(
    *,
    objective_id: str,
    disease_name: str | None = None,
    target_symbol: str,
    biologic_type: BiologicType = "monoclonal_antibody",
    design_mode: AntibodyDesignMode = "existing_antibody_ranking",
    antigen_context: AntigenContext | None = None,
    seed_sequences: Iterable[AntibodySequence] = (),
    seed_sequence_ids: Iterable[str] = (),
    approved_tool_packages: Iterable[str] = (),
    hard_constraints: Mapping[str, Any] | None = None,
    soft_constraints: Mapping[str, Any] | None = None,
    forbidden_motifs: Iterable[str] = (),
    review_requirements: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> AntibodyDesignObjective:
    seeds = list(seed_sequences)
    approved = {str(package) for package in approved_tool_packages}
    merged_hard = dict(hard_constraints or {})
    merged_soft = dict(soft_constraints or {})
    merged_metadata = dict(metadata or {})

    antigen_context_id = antigen_context.antigen_context_id if antigen_context else None
    source_backed_epitope = _has_source_backed_epitope(antigen_context)
    if design_mode == "epitope_context_design" and not source_backed_epitope:
        raise ValueError("Epitope-specific design requires source-backed epitope context.")

    if design_mode == "inverse_folding_plugin" and not _approved(
        "inverse_folding_plugin",
        approved,
        merged_hard,
        merged_metadata,
    ):
        raise ValueError("Inverse folding requires an approved structure/model plugin.")

    if design_mode in {"sequence_inpainting_plugin", "inverse_folding_plugin"} and not _approved(
        "external_antibody_generator",
        approved,
        merged_hard,
        merged_metadata,
    ):
        raise ValueError("External antibody generation requires approved tool package.")

    seed_ids = _append_unique([*seed_sequence_ids], [sequence.sequence_id for sequence in seeds])
    if design_mode == "cdr_mutation":
        if not seed_ids:
            raise ValueError("CDR mutation requires source-backed seed sequences.")
        if seeds:
            not_source_backed = [
                sequence.sequence_id
                for sequence in seeds
                if not _is_source_backed_seed(sequence)
            ]
            if not_source_backed:
                raise ValueError(
                    "CDR mutation requires source-backed seed sequences: "
                    + ", ".join(not_source_backed)
                )
        elif not merged_hard.get("source_backed_seed_sequences"):
            raise ValueError("CDR mutation requires source-backed seed sequence evidence.")
        merged_hard["source_backed_seed_sequences"] = True

    if source_backed_epitope:
        merged_hard["source_backed_epitope_context"] = True
    elif design_mode == "broad_target_context_design":
        merged_hard["broad_target_context_only"] = True

    merged_metadata.setdefault("source_backed_epitope", source_backed_epitope)
    merged_metadata.setdefault("approved_tool_packages", sorted(approved))

    return AntibodyDesignObjective(
        objective_id=objective_id,
        disease_name=disease_name,
        target_symbol=target_symbol,
        antigen_context_id=antigen_context_id,
        biologic_type=biologic_type,
        design_mode=design_mode,
        seed_sequence_ids=seed_ids,
        hard_constraints=merged_hard,
        soft_constraints=merged_soft,
        forbidden_motifs=[str(motif) for motif in forbidden_motifs],
        review_requirements=[str(requirement) for requirement in review_requirements],
        metadata=merged_metadata,
    )


def _has_source_backed_epitope(antigen_context: AntigenContext | None) -> bool:
    return bool(
        antigen_context
        and antigen_context.epitope_description
        and antigen_context.epitope_source
    )


def _approved(
    approval_name: str,
    approved_tool_packages: set[str],
    hard_constraints: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> bool:
    approved_names = {
        approval_name,
        f"approved_{approval_name}",
        "approved_tool_package",
    }
    if approved_tool_packages & approved_names:
        return True
    approvals = metadata.get("approved_tool_packages")
    if isinstance(approvals, list) and set(map(str, approvals)) & approved_names:
        return True
    return bool(
        hard_constraints.get(f"approved_{approval_name}")
        or hard_constraints.get("approved_tool_package")
    )


def _is_source_backed_seed(sequence: AntibodySequence) -> bool:
    if sequence.is_generated or sequence.source == "generated":
        return False
    if sequence.source_record_id:
        return True
    return bool(
        sequence.metadata.get("source_backed")
        or sequence.metadata.get("source_record_id")
        or sequence.metadata.get("imported_record_id")
    )


def _append_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for value in [*existing, *incoming]:
        if value not in merged:
            merged.append(value)
    return merged


__all__ = [
    "AntibodyDesignMode",
    "AntibodyDesignObjective",
    "DEFAULT_REVIEW_REQUIREMENTS",
    "build_antibody_design_objective",
]
