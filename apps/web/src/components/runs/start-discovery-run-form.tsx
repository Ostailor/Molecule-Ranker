"use client";

import { useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, FileArchive, FlaskConical, ListChecks, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { featureFlags } from "@/lib/feature-flags";

type StartDiscoveryRunFormProps = {
  projectName: string;
  projectObjective: string;
  diseaseOrArea: string;
  runHref: string;
};

const requiredDisclaimers = [
  "Generated molecules are computational hypotheses.",
  "Result bundle is not clinical validation.",
  "No medical advice.",
  "No lab protocols.",
  "No synthesis instructions.",
  "No dosing.",
];

export function StartDiscoveryRunForm({
  projectName,
  projectObjective,
  diseaseOrArea,
  runHref,
}: StartDiscoveryRunFormProps) {
  const [includeGenerated, setIncludeGenerated] = useState<boolean>(featureFlags.generationPreview);
  const [acknowledged, setAcknowledged] = useState(false);
  const [mockStarted, setMockStarted] = useState(false);

  const generatedCount = includeGenerated && featureFlags.generationPreview ? 3 : 0;
  const taskUsageLabel = useMemo(() => {
    if (includeGenerated && featureFlags.generationPreview) return "Standard preview estimate";
    return "Lower preview estimate";
  }, [includeGenerated]);

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_0.72fr]">
      <Card>
        <CardHeader title="Discovery workflow setup" eyebrow="PLACEHOLDER_V0_1_RUN_START" />
        <CardBody>
          <form className="grid gap-5">
            <label className="block">
              <span className="text-sm font-semibold text-ink-800">Disease / project objective</span>
              <textarea
                name="disease-project-objective"
                rows={4}
                className="mt-2 w-full rounded-product border-slatewash-200 text-sm leading-6 focus:border-teal-550 focus:ring-teal-550"
                defaultValue={`${diseaseOrArea}: ${projectObjective}`}
              />
            </label>

            <label className="block">
              <span className="text-sm font-semibold text-ink-800">Optional target focus</span>
              <input
                name="target-focus"
                className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                defaultValue="ExampleTargetA"
              />
            </label>

            <fieldset className="grid gap-3">
              <legend className="text-sm font-semibold text-ink-800">Workflow mode</legend>
              <label className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <input
                  type="radio"
                  name="workflow-mode"
                  value="dry-run-preview"
                  defaultChecked
                  className="mt-1 border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink-950">Dry run preview</span>
                  <span className="mt-1 block text-sm leading-6 text-ink-600">
                    Review configuration, usage estimate, and result bundle shape before any live workflow exists.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-3 rounded-product border border-slatewash-200 p-3">
                <input
                  type="radio"
                  name="workflow-mode"
                  value="read-only-evidence-workflow-placeholder"
                  className="mt-1 border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink-950">
                    Read-only evidence workflow placeholder
                  </span>
                  <span className="mt-1 block text-sm leading-6 text-ink-600">
                    Reserved for a later evidence review workflow. No backend execution is triggered in V0.1.
                  </span>
                </span>
              </label>
            </fieldset>

            <div className="grid gap-3 rounded-product border border-slatewash-200 p-3">
              {featureFlags.generationPreview ? (
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    name="include-generated-hypotheses"
                    checked={includeGenerated}
                    onChange={(event) => setIncludeGenerated(event.target.checked)}
                    className="mt-1 rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
                  />
                  <span>
                    <span className="block text-sm font-semibold text-ink-950">Include generated hypotheses</span>
                    <span className="mt-1 block text-sm leading-6 text-ink-600">
                      Adds a clearly labeled generated hypotheses section to the mock result bundle.
                    </span>
                  </span>
                </label>
              ) : (
                <p className="text-sm leading-6 text-ink-600">
                  Generated hypotheses are hidden by the current mock feature flag.
                </p>
              )}

              <label className="flex items-start gap-3">
                <input
                  type="checkbox"
                  name="export-result-bundle"
                  defaultChecked
                  className="mt-1 rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink-950">Prepare result bundle export</span>
                  <span className="mt-1 block text-sm leading-6 text-ink-600">
                    Creates a mock export-ready bundle link after the placeholder run state is created.
                  </span>
                </span>
              </label>
            </div>

            <label className="flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
              <input
                required
                type="checkbox"
                name="research-use-acknowledgement"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                className="mt-1 rounded border-amber-300 text-teal-550 focus:ring-teal-550"
              />
              <span>
                <span className="block text-sm font-semibold text-ink-950">Acknowledgement required</span>
                <span className="mt-1 block text-sm leading-6 text-ink-700">
                  I acknowledge this discovery run creates research-planning artifacts and hypotheses only.
                </span>
              </span>
            </label>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={!acknowledged}
                onClick={() => setMockStarted(true)}
                className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border border-teal-550 bg-teal-550 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:border-slatewash-200 disabled:bg-slatewash-200 disabled:text-ink-500"
              >
                <FlaskConical className="h-4 w-4" aria-hidden="true" />
                <span>Start mock run</span>
              </button>
              {mockStarted ? (
                <Button href={runHref} icon={ArrowRight} variant="secondary">
                  Open mock run
                </Button>
              ) : null}
            </div>

            {mockStarted ? (
              <div className="rounded-product border border-teal-450/40 bg-teal-450/10 p-3 text-sm leading-6 text-ink-700">
                Mock run state created locally for {projectName}. No backend execution was started.
              </div>
            ) : null}
          </form>
        </CardBody>
      </Card>

      <div className="grid content-start gap-6">
        <UsageEstimateCard generatedCount={generatedCount} taskUsageLabel={taskUsageLabel} />
        <RunDisclaimers />
        <ResearchUseBanner />
      </div>
    </div>
  );
}

function UsageEstimateCard({
  generatedCount,
  taskUsageLabel,
}: {
  generatedCount: number;
  taskUsageLabel: string;
}) {
  return (
    <Card>
      <CardHeader title="Usage estimate" eyebrow="Mock estimate" />
      <CardBody>
        <div className="grid gap-3">
          <EstimateRow icon={ListChecks} label="Discovery runs" value="1 discovery run" />
          <EstimateRow icon={CheckCircle2} label="Generated hypotheses" value={`${generatedCount} generated hypotheses`} />
          <EstimateRow icon={FileArchive} label="Estimated Codex task usage" value={taskUsageLabel} />
        </div>
      </CardBody>
    </Card>
  );
}

function EstimateRow({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ListChecks;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-product bg-slatewash-50 p-3">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-product bg-white text-teal-700 shadow-line">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="text-sm font-medium text-ink-700">{label}</span>
      </div>
      <span className="text-right text-sm font-semibold text-ink-950">{value}</span>
    </div>
  );
}

function RunDisclaimers() {
  return (
    <Card>
      <CardHeader title="Required boundaries" eyebrow="Research use" />
      <CardBody>
        <div className="mb-4 flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
          <p className="text-sm leading-6 text-ink-700">
            Keep the request bounded to research planning, candidate prioritization, evidence review, and result bundle
            preparation.
          </p>
        </div>
        <ul className="grid gap-2 text-sm leading-6 text-ink-700">
          {requiredDisclaimers.map((item) => (
            <li key={item} className="flex gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-teal-550" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
