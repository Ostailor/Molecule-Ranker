import { generatedHypotheses } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { MoleculeGlyph } from "@/components/product/molecule-glyph";
import { StatusBadge } from "@/components/ui/status-badge";

export function GeneratedGrid() {
  return (
    <Card>
      <CardHeader title="Generated hypotheses" eyebrow="No direct evidence" />
      <CardBody>
        <div className="grid gap-4 lg:grid-cols-3">
          {generatedHypotheses.map((hypothesis) => (
            <article key={hypothesis.id} className="rounded-product border border-slatewash-200 bg-white p-3">
              <MoleculeGlyph label={hypothesis.parentCandidateName} />
              <div className="mt-4 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-ink-950">{hypothesis.hypothesisType}</h2>
                  <p className="text-sm text-ink-600">Parent: {hypothesis.parentCandidateName}</p>
                </div>
                <StatusBadge tone="amber">No direct evidence</StatusBadge>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {hypothesis.warnings.map((warning) => (
                  <StatusBadge key={warning} tone="gray">
                    {warning}
                  </StatusBadge>
                ))}
              </div>
              <div className="mt-4 h-2 rounded-full bg-slatewash-100">
                <div className="h-2 rounded-full bg-teal-450" style={{ width: percent(hypothesis.score) }} />
              </div>
              <p className="mt-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">
                Hypothesis score {percent(hypothesis.score)}
              </p>
            </article>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}
