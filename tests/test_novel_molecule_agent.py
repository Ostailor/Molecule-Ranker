from __future__ import annotations

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.novel_molecule import STUB_MESSAGE, NovelMoleculeAgent
from molecule_ranker.schemas import MoleculeCandidate


def test_novel_molecule_agent_is_stub_and_does_not_alter_candidates():
    candidate = MoleculeCandidate(
        name="Existing candidate",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL_TEST"},
        known_targets=["TEST"],
        development_status=None,
        mechanism_of_action=None,
        evidence=[],
        score=None,
        score_breakdown=None,
        warnings=[],
    )
    context = PipelineContext(
        disease_input="Alzheimer disease",
        candidates=[candidate],
    )
    before = [item.model_dump(mode="json") for item in context.candidates]

    result = NovelMoleculeAgent().run(context)

    after = [item.model_dump(mode="json") for item in result.candidates]
    assert after == before
    assert result.candidates[0] is candidate
    assert STUB_MESSAGE in result.config["warnings"]
    assert result.traces[-1].agent_name == "NovelMoleculeAgent"
    assert result.traces[-1].output_summary == STUB_MESSAGE
    assert result.traces[-1].metadata["implemented"] is False
    assert "synthetic accessibility" in result.traces[-1].metadata["future_interface"]["filters"]
