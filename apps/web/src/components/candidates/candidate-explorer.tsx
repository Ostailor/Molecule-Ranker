"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowUpDown, ExternalLink, Filter, Star } from "lucide-react";
import type { ConfidenceBand, RankedCandidate } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";

type SortKey = "rank" | "score" | "confidence" | "evidence";

const confidenceWeight: Record<ConfidenceBand, number> = {
  High: 3,
  Medium: 2,
  Low: 1,
};

export function CandidateExplorer({
  candidates,
  projectId,
  runId,
}: {
  candidates: RankedCandidate[];
  projectId: string;
  runId: string;
}) {
  const modalities = Array.from(new Set(candidates.map((candidate) => candidate.modality)));
  const initiallySaved = candidates.filter((candidate) => candidate.status === "Saved for discussion").map((candidate) => candidate.id);
  const [modality, setModality] = useState("all");
  const [warningOnly, setWarningOnly] = useState(false);
  const [generatedOnly, setGeneratedOnly] = useState(false);
  const [savedOnly, setSavedOnly] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [savedIds, setSavedIds] = useState<string[]>(initiallySaved);

  const visibleCandidates = useMemo(() => {
    const filtered = candidates
      .filter((candidate) => modality === "all" || candidate.modality === modality)
      .filter((candidate) => !warningOnly || candidate.warnings.length > 0)
      .filter((candidate) => !generatedOnly || isGeneratedRelated(candidate))
      .filter((candidate) => !savedOnly || savedIds.includes(candidate.id));

    return [...filtered].sort((left, right) => compareCandidates(left, right, sortKey));
  }, [candidates, generatedOnly, modality, savedIds, savedOnly, sortKey, warningOnly]);

  function toggleSaved(candidateId: string) {
    setSavedIds((current) =>
      current.includes(candidateId) ? current.filter((item) => item !== candidateId) : [...current, candidateId],
    );
  }

  return (
    <Card>
      <CardHeader title="Candidate table" eyebrow="PLACEHOLDER_V0_1_CANDIDATE_TABLE" />
      <CardBody>
        <div className="mb-4 grid gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3 lg:grid-cols-[1fr_1fr_1.2fr]">
          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-ink-800">
              <Filter className="h-4 w-4" aria-hidden="true" />
              Modality
            </span>
            <select
              value={modality}
              onChange={(event) => setModality(event.target.value)}
              className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
            >
              <option value="all">All modalities</option>
              {modalities.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-ink-800">
              <ArrowUpDown className="h-4 w-4" aria-hidden="true" />
              Sort
            </span>
            <select
              value={sortKey}
              onChange={(event) => setSortKey(event.target.value as SortKey)}
              className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
            >
              <option value="rank">Rank</option>
              <option value="score">Prioritization score</option>
              <option value="confidence">Confidence</option>
              <option value="evidence">Evidence count</option>
            </select>
          </label>

          <div className="grid gap-2 sm:grid-cols-3">
            <FilterCheckbox label="Warning present" checked={warningOnly} onChange={setWarningOnly} />
            <FilterCheckbox label="Generated-related" checked={generatedOnly} onChange={setGeneratedOnly} />
            <FilterCheckbox label="Saved" checked={savedOnly} onChange={setSavedOnly} />
          </div>
        </div>

        <div className="overflow-x-auto rounded-product border border-slatewash-200">
          <table className="w-full min-w-[980px] border-collapse text-left text-sm">
            <thead className="bg-slatewash-50 text-xs font-semibold uppercase tracking-[0.08em] text-ink-600">
              <tr>
                {[
                  "Rank",
                  "Name",
                  "Modality",
                  "Score",
                  "Confidence",
                  "Evidence count",
                  "Flags/warnings",
                  "Saved status mock",
                  "Open detail",
                ].map((column) => (
                  <th key={column} className="border-b border-slatewash-200 px-3 py-3">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slatewash-200 bg-white">
              {visibleCandidates.map((candidate) => {
                const saved = savedIds.includes(candidate.id);
                return (
                  <tr key={candidate.id} className="hover:bg-slatewash-50/70">
                    <td className="px-3 py-3 font-semibold text-ink-950">{candidate.rank}</td>
                    <td className="px-3 py-3">
                      <Link
                        href={`/projects/${projectId}/runs/${runId}/candidates/${candidate.id}`}
                        className="font-semibold text-ink-950 hover:text-teal-700"
                      >
                        {candidate.name}
                      </Link>
                      <p className="mt-1 text-xs text-ink-500">{candidate.targetName}</p>
                    </td>
                    <td className="px-3 py-3 text-ink-700">{candidate.modality}</td>
                    <td className="px-3 py-3 font-semibold text-ink-950">{percent(candidate.score)}</td>
                    <td className="px-3 py-3">
                      <StatusBadge tone={candidate.confidence === "High" ? "green" : candidate.confidence === "Medium" ? "amber" : "gray"}>
                        {candidate.confidence}
                      </StatusBadge>
                    </td>
                    <td className="px-3 py-3 text-ink-700">{candidate.evidenceCount}</td>
                    <td className="px-3 py-3">
                      <div className="flex max-w-64 flex-wrap gap-1.5">
                        {candidate.warnings.map((warning) => (
                          <StatusBadge key={warning} tone="amber">
                            {warning}
                          </StatusBadge>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => toggleSaved(candidate.id)}
                        className="focus-ring inline-flex h-9 items-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 text-sm font-semibold text-ink-700 transition hover:border-teal-450/40 hover:bg-teal-450/5"
                      >
                        <Star className={saved ? "h-4 w-4 fill-teal-550 text-teal-550" : "h-4 w-4"} aria-hidden="true" />
                        <span>{saved ? "Saved mock" : "Save mock"}</span>
                      </button>
                    </td>
                    <td className="px-3 py-3">
                      <Button
                        href={`/projects/${projectId}/runs/${runId}/candidates/${candidate.id}`}
                        icon={ExternalLink}
                        variant="secondary"
                      >
                        Open
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {visibleCandidates.length === 0 ? (
          <p className="mt-4 rounded-product border border-slatewash-200 bg-white p-3 text-sm text-ink-600">
            No candidates match the current mock filters.
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}

function FilterCheckbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex min-h-10 items-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 text-sm font-semibold text-ink-700">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
      />
      {label}
    </label>
  );
}

function compareCandidates(left: RankedCandidate, right: RankedCandidate, sortKey: SortKey) {
  if (sortKey === "score") return right.score - left.score;
  if (sortKey === "confidence") return confidenceWeight[right.confidence] - confidenceWeight[left.confidence];
  if (sortKey === "evidence") return right.evidenceCount - left.evidenceCount;
  return left.rank - right.rank;
}

function isGeneratedRelated(candidate: RankedCandidate) {
  return (
    candidate.modality.toLowerCase().includes("generated") ||
    candidate.tags.some((tag) => tag.toLowerCase().includes("generated"))
  );
}
