from __future__ import annotations

from molecule_ranker.knowledge_graph.identifiers import (
    build_same_as_relations,
    detect_identifier_conflicts,
    entity_key_from_identifiers,
    merge_identifier_sets,
    normalize_identifier,
)
from molecule_ranker.knowledge_graph.ontology import (
    LocalOntologyMapper,
    map_to_ontology_terms,
)


def test_identifier_normalization_covers_external_and_internal_systems() -> None:
    assert normalize_identifier("opentargets_disease", " efo_0002508 ") == (
        "OpenTargetsDisease",
        "EFO_0002508",
    )
    assert normalize_identifier("mondo", " mondo:0005180 ") == ("MONDO", "MONDO:0005180")
    assert normalize_identifier("mesh", " d012345 ") == ("MeSH", "D012345")
    assert normalize_identifier("chembl_target", " chembl 1234 ") == (
        "ChEMBLTarget",
        "CHEMBL1234",
    )
    assert normalize_identifier("ensembl", " ensg00000198793.5 ") == (
        "Ensembl",
        "ENSG00000198793",
    )
    assert normalize_identifier("pmid", " PMID: 123456 ") == ("PMID", "123456")
    assert normalize_identifier("doi", " HTTPS://DOI.ORG/10.1000/ABC ") == (
        "DOI",
        "10.1000/abc",
    )
    assert normalize_identifier("candidate_id", " Candidate 1 ") == (
        "InternalCandidate",
        "Candidate 1",
    )


def test_entity_key_prefers_stable_identifier_priority() -> None:
    assert (
        entity_key_from_identifiers(
            "target",
            {"UniProt": "P27338", "ChEMBLTarget": "CHEMBL2039"},
        )
        == "target:ChEMBLTarget:CHEMBL2039"
    )
    assert (
        entity_key_from_identifiers("molecule", {"InChIKey": " abc-def ", "PubChemCID": "123"})
        == "molecule:InChIKey:ABC-DEF"
    )
    assert entity_key_from_identifiers("project", {"artifact_id": "artifact-1"}) == (
        "project:ArtifactID:artifact-1"
    )


def test_merge_identifier_sets_detects_conflicts_and_requires_review() -> None:
    merged = merge_identifier_sets(
        {"ChEMBLMolecule": "CHEMBL25", "PubChemCID": "2244"},
        {"chembl_molecule": "CHEMBL25", "pubchem": "9999", "pmid": "123456"},
    )

    assert merged.identifiers["ChEMBLMolecule"] == "CHEMBL25"
    assert merged.identifiers["PMID"] == "123456"
    assert merged.review_required is True
    assert merged.warnings
    assert merged.conflicts[0].prefix == "PubChemCID"

    conflicts = detect_identifier_conflicts(
        {"PubChemCID": "2244"},
        {"pubchem_cid": "9999"},
    )
    assert conflicts[0].existing_value == "2244"
    assert conflicts[0].incoming_value == "9999"


def test_same_as_relations_are_deterministic_or_user_confirmed_only() -> None:
    relations = build_same_as_relations(
        [
            ("target:a", {"HGNC": "6834"}),
            ("target:b", {"hgnc": "6834"}),
            ("target:c", {"HGNC": "9999"}),
        ],
        mapping_method="deterministic",
        source_artifact_id="mapping-artifact",
    )

    assert len(relations) == 1
    assert relations[0].predicate == "same_as"
    assert relations[0].relation_type == "ontology_mapping"
    assert relations[0].metadata["mapping_method"] == "deterministic"

    codex_relations = build_same_as_relations(
        [("target:a", {"HGNC": "6834"}), ("target:b", {"hgnc": "6834"})],
        mapping_method="codex_suggested",
    )
    assert codex_relations == []

    reviewed = build_same_as_relations(
        [("target:a", {"HGNC": "6834"}), ("target:b", {"hgnc": "6834"})],
        mapping_method="codex_suggested",
        user_confirmed=True,
    )
    assert reviewed[0].metadata["mapping_method"] == "user_confirmed"


def test_local_ontology_mapping_and_rdf_export_do_not_require_downloads() -> None:
    mapper = LocalOntologyMapper(
        {
            ("disease", "parkinson disease"): {
                "MONDO": "MONDO:0005180",
                "EFO": "EFO_0002508",
                "MeSH": "D010300",
            }
        }
    )

    mappings = map_to_ontology_terms(
        "disease",
        "Parkinson disease",
        mapper=mapper,
        source_artifact_id="local-ontology",
    )

    assert mappings[0].identifiers["MONDO"] == "MONDO:0005180"
    assert mappings[0].review_required is False
    triples = mapper.export_rdf_triples()
    assert "mr:ontology/disease/parkinson-disease mr:mapsTo mondo:MONDO_0005180 ." in triples

    try:
        mapper.import_obo_owl_rdf("ontology.owl")
    except NotImplementedError as exc:
        assert "Optional OBO/OWL/RDF import" in str(exc)
