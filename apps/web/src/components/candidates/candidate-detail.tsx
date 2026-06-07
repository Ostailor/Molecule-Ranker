import { candidates } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { MoleculeGlyph } from "@/components/product/molecule-glyph";
import { StatusBadge } from "@/components/ui/status-badge";

export function CandidateDetail() {
  const candidate = candidates[0];

  return (
    <div className="grid gap-6 xl:grid-cols-[0.75fr_1.25fr]">
      <Card>
        <CardHeader title={candidate.name} eyebrow="Candidate file" />
        <CardBody>
          <MoleculeGlyph label={candidate.name} />
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-center justify-between border-b border-slatewash-200 pb-2">
              <span className="text-ink-600">Target</span>
              <span className="font-semibold text-ink-950">{candidate.targetName}</span>
            </div>
            <div className="flex items-center justify-between border-b border-slatewash-200 pb-2">
              <span className="text-ink-600">Modality</span>
              <span className="font-semibold text-ink-950">{candidate.modality}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-600">Status</span>
              <span className="font-semibold text-ink-950">{candidate.status}</span>
            </div>
          </div>
        </CardBody>
      </Card>
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-3">
          <Metric label="Prioritization score" value={percent(candidate.score)} detail="Synthetic UI demo score" />
          <Metric label="Evidence items" value={String(candidate.evidenceCount)} detail="Synthetic rows linked" />
          <div className="rounded-product border border-slatewash-200 bg-white p-4 shadow-line">
            <p className="text-sm font-medium text-ink-600">Confidence</p>
            <StatusBadge tone="amber" className="mt-3">
              {candidate.confidence}
            </StatusBadge>
            <p className="mt-3 text-sm leading-6 text-ink-600">Reviewer must treat all score bands as demo-only placeholders.</p>
          </div>
        </div>
        <Card>
          <CardHeader title="Research notes" eyebrow="Review" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              {candidate.name} is treated as a computational hypothesis for research planning. Advancement decisions
              require evidence review, limitation checks, and qualified human review.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {candidate.warnings.map((warning) => (
                <StatusBadge key={warning} tone="amber">
                  {warning}
                </StatusBadge>
              ))}
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
