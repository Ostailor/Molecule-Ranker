"use client";

import { useState } from "react";
import { Download, FileText, Star } from "lucide-react";
import type { EvidenceItem, RankedCandidate } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { MoleculeGlyph } from "@/components/product/molecule-glyph";
import { StatusBadge } from "@/components/ui/status-badge";

export function CandidateDetailView({
  candidate,
  evidence,
  projectName,
  runName,
}: {
  candidate: RankedCandidate;
  evidence: EvidenceItem[];
  projectName: string;
  runName: string;
}) {
  const [saved, setSaved] = useState(candidate.status === "Saved for discussion");
  const [exportPrepared, setExportPrepared] = useState(false);

  return (
    <div className="grid gap-6 xl:grid-cols-[0.78fr_1.22fr]">
      <div className="space-y-6">
        <Card>
          <CardHeader title="Summary" eyebrow="Candidate file" />
          <CardBody>
            <MoleculeGlyph label={candidate.name} />
            <div className="mt-4 space-y-3 text-sm">
              <SummaryRow label="Project" value={projectName} />
              <SummaryRow label="Run" value={runName} />
              <SummaryRow label="Target focus" value={candidate.targetName} />
              <SummaryRow label="Modality" value={candidate.modality} />
              <SummaryRow label="Research review status" value={candidate.status} />
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setSaved((current) => !current)}
                className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border border-teal-550 bg-teal-550 px-3 text-sm font-semibold text-white transition hover:bg-teal-700"
              >
                <Star className={saved ? "h-4 w-4 fill-white" : "h-4 w-4"} aria-hidden="true" />
                <span>{saved ? "Saved locally" : "Save mock"}</span>
              </button>
              <button
                type="button"
                onClick={() => setExportPrepared(true)}
                className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 text-sm font-semibold text-ink-700 transition hover:border-teal-450/40 hover:bg-teal-450/5"
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                <span>Export candidate placeholder</span>
              </button>
            </div>
            {exportPrepared ? (
              <p className="mt-4 rounded-product border border-teal-450/40 bg-teal-450/10 p-3 text-sm leading-6 text-ink-700">
                Candidate export placeholder prepared locally. No file was created and no backend request was made.
              </p>
            ) : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Identifiers placeholder" eyebrow="PLACEHOLDER_V0_1_IDENTIFIERS" />
          <CardBody>
            <dl className="grid gap-3 text-sm">
              <SummaryRow label="Internal UI demo ID" value={candidate.id} />
              <SummaryRow label="External registry IDs" value="Not connected in V0.1" />
              <SummaryRow label="Source identifiers" value="No source-backed identifiers imported" />
            </dl>
          </CardBody>
        </Card>
      </div>

      <div className="space-y-6">
        <section className="grid gap-4 md:grid-cols-3">
          <Metric label="Prioritization score" value={percent(candidate.score)} detail="Synthetic UI demo score" />
          <Metric label="Evidence items" value={String(candidate.evidenceCount)} detail="Preview rows linked" />
          <div className="rounded-product border border-slatewash-200 bg-white p-4 shadow-line">
            <p className="text-sm font-medium text-ink-600">Confidence</p>
            <StatusBadge tone={candidate.confidence === "High" ? "green" : candidate.confidence === "Medium" ? "amber" : "gray"} className="mt-3">
              {candidate.confidence}
            </StatusBadge>
            <p className="mt-3 text-sm leading-6 text-ink-600">Score bands are placeholders for research review.</p>
          </div>
        </section>

        <Card>
          <CardHeader title="Evidence list preview" eyebrow="Synthetic evidence" />
          <CardBody>
            {evidence.length > 0 ? (
              <div className="grid gap-3">
                {evidence.map((item) => (
                  <article key={item.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <h2 className="text-sm font-semibold text-ink-950">{item.title}</h2>
                      <StatusBadge tone="amber">{item.confidence}</StatusBadge>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-ink-600">{item.summary}</p>
                    <p className="mt-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">
                      {item.sourceType}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="rounded-product border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-ink-700">
                No evidence items are linked to this synthetic candidate.
              </p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Provenance preview" eyebrow="Synthetic metadata" />
          <CardBody>
            <div className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" aria-hidden="true" />
              <p className="text-sm leading-6 text-ink-700">
                This candidate record is marked synthetic and for UI demo only. Imported provenance, source identifiers,
                and reviewer attribution are placeholders for a later release.
              </p>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Warnings/limitations" eyebrow="Research review" />
          <CardBody>
            <div className="flex flex-wrap gap-2">
              {candidate.warnings.map((warning) => (
                <StatusBadge key={warning} tone="amber">
                  {warning}
                </StatusBadge>
              ))}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Notes placeholder" eyebrow="PLACEHOLDER_V0_1_NOTES" />
          <CardBody>
            <textarea
              rows={4}
              className="w-full rounded-product border-slatewash-200 text-sm leading-6 focus:border-teal-550 focus:ring-teal-550"
              defaultValue="Local notes placeholder for research review. Not stored in V0.1."
            />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slatewash-200 pb-2 last:border-b-0 last:pb-0">
      <dt className="text-ink-600">{label}</dt>
      <dd className="text-right font-semibold text-ink-950">{value}</dd>
    </div>
  );
}
