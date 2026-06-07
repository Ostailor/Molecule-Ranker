"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, Filter, ShieldAlert } from "lucide-react";
import type { ConfidenceBand, EvidenceItem, RankedCandidate } from "@/lib/mock-data";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";

export function EvidenceExplorer({
  evidenceItems,
  candidates,
  projectId,
  runId,
}: {
  evidenceItems: EvidenceItem[];
  candidates: RankedCandidate[];
  projectId: string;
  runId: string;
}) {
  const sourceTypes = Array.from(new Set(evidenceItems.map((item) => item.sourceType)));
  const candidateOptions = candidates.filter((candidate) =>
    evidenceItems.some((item) => item.candidateId === candidate.id),
  );
  const [sourceType, setSourceType] = useState("all");
  const [candidateId, setCandidateId] = useState("all");
  const [confidence, setConfidence] = useState<"all" | ConfidenceBand>("all");
  const [warningOnly, setWarningOnly] = useState(false);

  const visibleEvidence = useMemo(() => {
    return evidenceItems
      .filter((item) => sourceType === "all" || item.sourceType === sourceType)
      .filter((item) => candidateId === "all" || item.candidateId === candidateId)
      .filter((item) => confidence === "all" || item.confidence === confidence)
      .filter((item) => !warningOnly || hasEvidenceWarning(item, candidates));
  }, [candidateId, candidates, confidence, evidenceItems, sourceType, warningOnly]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Synthetic evidence notice" eyebrow="PLACEHOLDER_V0_1_EVIDENCE" />
        <CardBody>
          <div className="flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
            <p className="text-sm leading-6 text-ink-700">
              This V0.1 page uses synthetic UI data. Real source-backed evidence will be connected in a later release.
            </p>
          </div>
          <p className="mt-3 text-sm leading-6 text-ink-600">
            Use this page to review source provenance placeholders, independently verify evidence when real sources are
            connected, and remember that lack of evidence is not evidence of absence.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Evidence item list" eyebrow="Synthetic UI data" />
        <CardBody>
          <div className="mb-4 grid gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3 lg:grid-cols-4">
            <SelectFilter
              label="Source type"
              value={sourceType}
              onChange={setSourceType}
              options={["all", ...sourceTypes]}
              optionLabel={(value) => (value === "all" ? "All source types" : value)}
            />
            <SelectFilter
              label="Candidate"
              value={candidateId}
              onChange={setCandidateId}
              options={["all", ...candidateOptions.map((candidate) => candidate.id)]}
              optionLabel={(value) => (value === "all" ? "All candidates" : candidateName(value, candidates))}
            />
            <SelectFilter
              label="Confidence"
              value={confidence}
              onChange={(value) => setConfidence(value as "all" | ConfidenceBand)}
              options={["all", "High", "Medium", "Low"]}
              optionLabel={(value) => (value === "all" ? "All confidence levels" : value)}
            />
            <label className="flex min-h-10 items-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 text-sm font-semibold text-ink-700">
              <input
                type="checkbox"
                checked={warningOnly}
                onChange={(event) => setWarningOnly(event.target.checked)}
                className="rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
              />
              Warning
            </label>
          </div>

          <div className="grid gap-4">
            {visibleEvidence.map((item) => {
              const candidate = candidates.find((record) => record.id === item.candidateId);
              const limitations = buildLimitations(item, candidate);
              return (
                <article key={item.id} className="rounded-product border border-slatewash-200 bg-white p-4 shadow-line">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">
                        {item.sourceType}
                      </p>
                      <h2 className="mt-1 text-base font-semibold text-ink-950">{item.title}</h2>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge tone={item.confidence === "High" ? "green" : item.confidence === "Medium" ? "amber" : "gray"}>
                        {item.confidence}
                      </StatusBadge>
                      <StatusBadge tone="amber">Synthetic</StatusBadge>
                    </div>
                  </div>

                  <p className="mt-3 text-sm leading-6 text-ink-700">{item.summary}</p>

                  <div className="mt-4 grid gap-3 lg:grid-cols-[0.8fr_1fr_1fr]">
                    <InfoBlock title="Related candidate">
                      {candidate ? (
                        <Link
                          href={`/projects/${projectId}/runs/${runId}/candidates/${candidate.id}`}
                          className="inline-flex items-center gap-2 font-semibold text-teal-700 hover:text-teal-900"
                        >
                          {candidate.name}
                          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                        </Link>
                      ) : (
                        <span>No candidate linked</span>
                      )}
                    </InfoBlock>
                    <InfoBlock title="Provenance placeholder">
                      Synthetic provenance row only. Real source metadata, citation fields, and import checks are not
                      connected in V0.1.
                    </InfoBlock>
                    <InfoBlock title="Limitations">
                      <ul className="grid gap-1">
                        {limitations.map((limitation) => (
                          <li key={limitation} className="flex gap-2">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" aria-hidden="true" />
                            <span>{limitation}</span>
                          </li>
                        ))}
                      </ul>
                    </InfoBlock>
                  </div>
                </article>
              );
            })}
          </div>

          {visibleEvidence.length === 0 ? (
            <p className="mt-4 rounded-product border border-slatewash-200 bg-white p-3 text-sm text-ink-600">
              No synthetic evidence items match the current filters.
            </p>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

function SelectFilter({
  label,
  value,
  onChange,
  options,
  optionLabel,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  optionLabel: (value: string) => string;
}) {
  return (
    <label className="block">
      <span className="flex items-center gap-2 text-sm font-semibold text-ink-800">
        <Filter className="h-4 w-4" aria-hidden="true" />
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function InfoBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3 text-sm leading-6 text-ink-700">
      <p className="mb-1 text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">{title}</p>
      {children}
    </div>
  );
}

function candidateName(candidateId: string, candidates: RankedCandidate[]) {
  return candidates.find((candidate) => candidate.id === candidateId)?.name ?? candidateId;
}

function hasEvidenceWarning(item: EvidenceItem, candidates: RankedCandidate[]) {
  const candidate = candidates.find((record) => record.id === item.candidateId);
  return item.synthetic || item.confidence === "Low" || Boolean(candidate?.warnings.length);
}

function buildLimitations(item: EvidenceItem, candidate: RankedCandidate | undefined) {
  const limitations = ["Synthetic UI data only", "Independently verify evidence before research use"];
  if (item.confidence === "Low") limitations.push("Low confidence placeholder");
  if (candidate?.warnings.length) limitations.push("Related candidate has warnings/limitations");
  return limitations;
}
