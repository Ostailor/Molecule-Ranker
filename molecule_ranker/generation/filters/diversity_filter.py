from __future__ import annotations

from rdkit import Chem

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationConfig


class DiversityFilter:
    """Cluster generated molecules by fingerprint similarity and cap each cluster."""

    def filter(
        self,
        generated: list[GeneratedMolecule],
        *,
        config: GenerationConfig,
    ) -> tuple[list[GeneratedMolecule], list[GeneratedMolecule]]:
        retained: list[GeneratedMolecule] = []
        rejected: list[GeneratedMolecule] = []
        clusters: list[dict[str, object]] = []

        for candidate in sorted(
            generated,
            key=lambda item: item.generation_score or 0.0,
            reverse=True,
        ):
            mol = mol_from_smiles(candidate.canonical_smiles or candidate.smiles)
            if mol is None:
                rejected.append(candidate)
                continue
            cluster_index = self._cluster_index(mol, clusters, config)
            if cluster_index is None:
                cluster_index = len(clusters)
                clusters.append({"representative": mol, "members": []})
            cluster_id = f"cluster-{cluster_index + 1}"
            updated = candidate.model_copy(update={"diversity_cluster": cluster_id})
            members = clusters[cluster_index]["members"]
            assert isinstance(members, list)
            if len(members) >= config.max_generated_per_diversity_cluster:
                rejected.append(updated)
                continue
            members.append(updated.generated_id)
            retained.append(updated)
        return retained, rejected

    def _cluster_index(
        self,
        mol: Chem.Mol,
        clusters: list[dict[str, object]],
        config: GenerationConfig,
    ) -> int | None:
        for index, cluster in enumerate(clusters):
            representative = cluster["representative"]
            if not isinstance(representative, Chem.Mol):
                continue
            similarity = tanimoto_similarity(mol, representative)
            if similarity >= config.diversity_similarity_threshold:
                return index
        return None
