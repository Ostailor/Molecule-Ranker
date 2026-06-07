"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Filter, FlaskConical, ShieldAlert } from "lucide-react";
import type { GeneratedHypothesis, RankedCandidate } from "@/lib/mock-data";
import { percent } from "@/lib/formatting";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";

type ConfidenceFilter = "all" | "Medium" | "Low";

const requiredBanner =
  "Generated hypotheses are computational structures or ideas. They are not known actives, not validated molecules, and not evidence of safety, efficacy, binding, or therapeutic value.";

export function GeneratedHypothesesExplorer({
  candidates,
  featureEnabled,
  hypotheses,
}: {
  candidates: RankedCandidate[];
  featureEnabled: boolean;
  hypotheses: GeneratedHypothesis[];
}) {
  const parentCandidates = Array.from(new Set(hypotheses.map((item) => item.parentCandidateName)));
  const [parentCandidate, setParentCandidate] = useState("all");
  const [confidence, setConfidence] = useState<ConfidenceFilter>("all");
  const [warningOnly, setWarningOnly] = useState(false);

  const visibleHypotheses = useMemo(() => {
    return hypotheses
      .filter((item) => parentCandidate === "all" || item.parentCandidateName === parentCandidate)
      .filter((item) => confidence === "all" || confidenceLabel(item.score) === confidence)
      .filter((item) => !warningOnly || item.warnings.length > 0);
  }, [confidence, hypotheses, parentCandidate, warningOnly]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Generated hypothesis warning" eyebrow="Required review boundary" />
        <CardBody>
          <div className="flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
            <p className="text-sm leading-6 text-ink-700">{requiredBanner}</p>
          </div>
        </CardBody>
      </Card>

      {!featureEnabled ? <FeatureDisabledState /> : null}
      {featureEnabled && hypotheses.length === 0 ? <NoGeneratedHypothesesState /> : null}

      {featureEnabled && hypotheses.length > 0 ? (
        <Card>
          <CardHeader title="Generated hypothesis cards" eyebrow="PLACEHOLDER_V0_1_GENERATED" />
          <CardBody>
            <div className="mb-4 grid gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3 lg:grid-cols-3">
              <SelectFilter
                label="Parent candidate"
                value={parentCandidate}
                onChange={setParentCandidate}
                options={["all", ...parentCandidates]}
                optionLabel={(value) => (value === "all" ? "All parent candidates" : value)}
              />
              <SelectFilter
                label="Confidence"
                value={confidence}
                onChange={(value) => setConfidence(value as ConfidenceFilter)}
                options={["all", "Medium", "Low"]}
                optionLabel={(value) => (value === "all" ? "All confidence labels" : value)}
              />
              <label className="flex min-h-10 items-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 text-sm font-semibold text-ink-700">
                <input
                  type="checkbox"
                  checked={warningOnly}
                  onChange={(event) => setWarningOnly(event.target.checked)}
                  className="rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                Warnings only
              </label>
            </div>

            {visibleHypotheses.length > 0 ? (
              <div className="grid gap-4 lg:grid-cols-3">
                {visibleHypotheses.map((hypothesis) => (
                  <GeneratedHypothesisCard
                    key={hypothesis.id}
                    hypothesis={hypothesis}
                    parentCandidate={candidates.find((candidate) => candidate.name === hypothesis.parentCandidateName)}
                  />
                ))}
              </div>
            ) : (
              <FilteredOutState />
            )}
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}

function GeneratedHypothesisCard({
  hypothesis,
  parentCandidate,
}: {
  hypothesis: GeneratedHypothesis;
  parentCandidate?: RankedCandidate;
}) {
  const confidence = confidenceLabel(hypothesis.score);

  return (
    <article className="rounded-product border border-slatewash-200 bg-white p-4 shadow-line">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">Generated hypothesis</p>
          <h2 className="mt-1 text-base font-semibold text-ink-950">{hypothesis.hypothesisType}</h2>
        </div>
        <FlaskConical className="h-5 w-5 shrink-0 text-teal-700" aria-hidden="true" />
      </div>

      <dl className="mt-4 grid gap-3 text-sm">
        <SummaryRow label="Parent candidate" value={hypothesis.parentCandidateName} />
        <SummaryRow label="Parent modality" value={parentCandidate?.modality ?? "Synthetic placeholder"} />
        <SummaryRow label="Hypothesis type" value={hypothesis.hypothesisType} />
      </dl>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-product bg-slatewash-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Score</p>
          <p className="mt-1 text-xl font-semibold text-ink-950">{percent(hypothesis.score)}</p>
        </div>
        <div className="rounded-product bg-slatewash-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Confidence</p>
          <StatusBadge tone={confidence === "Medium" ? "amber" : "gray"} className="mt-2">
            {confidence}
          </StatusBadge>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <StatusBadge tone="amber">No direct evidence</StatusBadge>
        <StatusBadge tone={hypothesis.noDirectEvidence ? "amber" : "green"}>
          noDirectEvidence: {String(hypothesis.noDirectEvidence)}
        </StatusBadge>
      </div>

      <div className="mt-4">
        <p className="text-sm font-semibold text-ink-950">Warnings</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {hypothesis.warnings.map((warning) => (
            <StatusBadge key={warning} tone="gray">
              {warning}
            </StatusBadge>
          ))}
        </div>
      </div>

      <div className="mt-4 flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
        <p className="text-sm leading-6 text-ink-700">
          Required human review: separate this hypothesis from imported evidence and document limitations before any
          research-planning use.
        </p>
      </div>
    </article>
  );
}

function FeatureDisabledState() {
  return (
    <Card>
      <CardHeader title="Generated hypotheses disabled" eyebrow="Feature state" />
      <CardBody>
        <p className="text-sm leading-6 text-ink-600">
          The generated hypotheses section is hidden by the current mock feature setting. No generated cards are shown in
          this state.
        </p>
      </CardBody>
    </Card>
  );
}

function NoGeneratedHypothesesState() {
  return (
    <Card>
      <CardHeader title="No generated hypotheses" eyebrow="Empty state" />
      <CardBody>
        <p className="text-sm leading-6 text-ink-600">
          This mock run has no generated hypotheses to review. Candidate ranking and evidence sections may still be
          available in the result bundle.
        </p>
      </CardBody>
    </Card>
  );
}

function FilteredOutState() {
  return (
    <div className="rounded-product border border-slatewash-200 bg-white p-4 text-sm leading-6 text-ink-600">
      All generated hypotheses are filtered out. Adjust parent candidate, confidence, or warning filters to show cards.
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

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slatewash-200 pb-2 last:border-b-0 last:pb-0">
      <dt className="text-ink-600">{label}</dt>
      <dd className="text-right font-semibold text-ink-950">{value}</dd>
    </div>
  );
}

function confidenceLabel(score: number): "Medium" | "Low" {
  return score >= 0.7 ? "Medium" : "Low";
}

