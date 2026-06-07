import Link from "next/link";
import { candidates, runs } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { projectId } from "@/lib/routes";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { MoleculeGlyph } from "@/components/product/molecule-glyph";
import { StatusBadge } from "@/components/ui/status-badge";

export function CandidateRanking() {
  const run = runs[0];

  return (
    <Card>
      <CardHeader title="Ranked candidates" eyebrow="Evidence-weighted score" />
      <CardBody>
        <DataTable
          columns={["Candidate", "Target", "Modality", "Score", "Confidence", "Evidence", "Status"]}
          rows={candidates.map((candidate) => [
            <Link
              key={candidate.id}
              href={`/projects/${projectId}/runs/${run.id}/candidates/${candidate.id}`}
              className="flex items-center gap-3 font-semibold text-ink-950 hover:text-teal-700"
            >
              <MoleculeGlyph label={candidate.name} dense className="hidden h-14 w-20 shrink-0 sm:block" />
              <span>{candidate.name}</span>
            </Link>,
            candidate.targetName,
            candidate.modality,
            percent(candidate.score),
            <StatusBadge
              key={`${candidate.id}-confidence`}
              tone={candidate.confidence === "High" ? "green" : candidate.confidence === "Medium" ? "amber" : "gray"}
            >
              {candidate.confidence}
            </StatusBadge>,
            candidate.evidenceCount,
            <StatusBadge key={`${candidate.id}-status`} tone="gray">
              {candidate.status}
            </StatusBadge>,
          ])}
        />
      </CardBody>
    </Card>
  );
}
