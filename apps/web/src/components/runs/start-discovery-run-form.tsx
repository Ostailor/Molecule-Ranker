"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, FileArchive, FlaskConical, ListChecks, ShieldAlert } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { productFeatureFlags } from "@/lib/product/feature-flags";

type StartDiscoveryRunFormProps = {
  projectId: string;
  projectObjective: string;
  diseaseOrArea: string;
  targetFocus?: string | null;
  usageRemaining?: number | null;
  usageLimit?: number | null;
  usageBlockedMessage?: string | null;
  allowReadOnlyLive?: boolean;
};

const requiredDisclaimers = [
  "No patient-specific info.",
  "No medical advice.",
  "Not a lab protocol.",
  "Generated hypotheses are computational only.",
  "Result bundle is not clinical validation.",
  "No synthesis instructions.",
  "No dosing.",
  "Antibody generation disabled.",
  "External writes disabled.",
  "Write-approved mode disabled.",
];

const maxGeneratedHypothesisLimit = 3;

export function StartDiscoveryRunForm({
  projectId,
  projectObjective,
  diseaseOrArea,
  targetFocus,
  usageRemaining,
  usageLimit,
  usageBlockedMessage,
  allowReadOnlyLive = false,
}: StartDiscoveryRunFormProps) {
  const router = useRouter();
  const [includeGenerated, setIncludeGenerated] = useState(false);
  const [maxGeneratedHypotheses, setMaxGeneratedHypotheses] = useState(maxGeneratedHypothesisLimit);
  const [acknowledged, setAcknowledged] = useState(false);
  const [status, setStatus] = useState<"idle" | "submitting" | "redirecting" | "error">("idle");
  const [message, setMessage] = useState<string | null>(usageBlockedMessage ?? null);
  const usageBlocked = Boolean(usageBlockedMessage);

  const generatedCount = includeGenerated && productFeatureFlags.generatedHypothesesViewer ? maxGeneratedHypotheses : 0;
  const taskUsageLabel = useMemo(() => {
    if (includeGenerated && productFeatureFlags.generatedHypothesesViewer) return "Standard preview estimate";
    return "Lower preview estimate";
  }, [includeGenerated]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!acknowledged) {
      setStatus("error");
      setMessage("Acknowledge the research-use boundary before starting a run.");
      return;
    }

    if (usageBlocked) return;

    const formData = new FormData(event.currentTarget);
    setStatus("submitting");
    setMessage(null);

    try {
      const response = await fetch(`/api/product/projects/${projectId}/runs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          disease_or_goal: String(formData.get("disease_or_goal") ?? ""),
          target_focus: String(formData.get("target_focus") ?? ""),
          mode: String(formData.get("workflow_mode") ?? "dry_run"),
          include_generated_hypotheses:
            productFeatureFlags.generatedHypothesesViewer && formData.get("include_generated_hypotheses") === "on",
          max_generated_hypotheses: Number(formData.get("max_generated_hypotheses") ?? maxGeneratedHypothesisLimit),
          prepare_result_bundle: true,
          acknowledgement: acknowledged,
        }),
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok || !payload?.ok) {
        setStatus("error");
        setMessage(payload?.error?.message ?? "Could not start the discovery run.");
        return;
      }

      const runId = payload.data.run.id;
      setStatus("redirecting");
      setMessage("Discovery run created. Redirecting to run status.");
      router.push(`/projects/${projectId}/runs/${runId}`);
    } catch {
      setStatus("error");
      setMessage("Could not start the discovery run.");
    }
  }

  if (!productFeatureFlags.discoveryRunsPlaceholder) {
    return (
      <div className="grid gap-6 xl:grid-cols-[1fr_0.72fr]">
        <Card>
          <CardHeader title="Discovery workflow setup" eyebrow="Feature disabled" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Discovery runs are hidden by the current product feature flag. No workflow execution is available.
            </p>
          </CardBody>
        </Card>
        <ResearchUseBanner />
      </div>
    );
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_0.72fr]">
      <Card>
        <CardHeader title="Discovery workflow setup" eyebrow="V0.3 bounded runner" />
        <CardBody>
          <form className="grid gap-5" onSubmit={onSubmit}>
            <label className="block">
              <span className="text-sm font-semibold text-ink-800">Disease or goal</span>
              <textarea
                name="disease_or_goal"
                rows={4}
                className="mt-2 w-full rounded-product border-slatewash-200 text-sm leading-6 focus:border-teal-550 focus:ring-teal-550"
                defaultValue={`${diseaseOrArea}: ${projectObjective}`}
              />
            </label>

            <label className="block">
              <span className="text-sm font-semibold text-ink-800">Optional target focus</span>
              <input
                name="target_focus"
                className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                defaultValue={targetFocus ?? ""}
              />
            </label>

            <fieldset className="grid gap-3">
              <legend className="text-sm font-semibold text-ink-800">Workflow mode</legend>
              <label className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <input
                  type="radio"
                  name="workflow_mode"
                  value="dry_run"
                  defaultChecked
                  className="mt-1 border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink-950">Dry run preview</span>
                  <span className="mt-1 block text-sm leading-6 text-ink-600">
                    Execute the bounded product-safe wrapper without external writes or live integrations.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-3 rounded-product border border-slatewash-200 p-3">
                <input
                  type="radio"
                  name="workflow_mode"
                  value="mocked"
                  className="mt-1 border-slatewash-300 text-teal-550 focus:ring-teal-550"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink-950">Mocked discovery workflow</span>
                  <span className="mt-1 block text-sm leading-6 text-ink-600">
                    Create deterministic product-safe status and result artifacts for local review.
                  </span>
                </span>
              </label>
              {allowReadOnlyLive ? (
                <label className="flex items-start gap-3 rounded-product border border-slatewash-200 p-3">
                  <input
                    type="radio"
                    name="workflow_mode"
                    value="read_only_live"
                    className="mt-1 border-slatewash-300 text-teal-550 focus:ring-teal-550"
                  />
                  <span>
                    <span className="block text-sm font-semibold text-ink-950">Read-only live workflow</span>
                    <span className="mt-1 block text-sm leading-6 text-ink-600">
                      Uses only the reviewed read-only engine path with external writes disabled.
                    </span>
                  </span>
                </label>
              ) : null}
            </fieldset>

            <div className="grid gap-3 rounded-product border border-slatewash-200 p-3">
              {productFeatureFlags.generatedHypothesesViewer ? (
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    name="include_generated_hypotheses"
                    checked={includeGenerated}
                    onChange={(event) => setIncludeGenerated(event.target.checked)}
                    className="mt-1 rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
                  />
                  <span>
                    <span className="block text-sm font-semibold text-ink-950">Include generated hypotheses</span>
                    <span className="mt-1 block text-sm leading-6 text-ink-600">
                      Adds a clearly labeled generated hypotheses summary to the product-safe result bundle.
                    </span>
                  </span>
                </label>
              ) : (
                <p className="text-sm leading-6 text-ink-600">
                  Generated hypotheses are hidden by the current mock feature flag.
                </p>
              )}

              <label className="block">
                <span className="text-sm font-semibold text-ink-800">Max generated hypotheses</span>
                <input
                  type="number"
                  name="max_generated_hypotheses"
                  min={0}
                  max={maxGeneratedHypothesisLimit}
                  step={1}
                  value={maxGeneratedHypotheses}
                  disabled={!includeGenerated || !productFeatureFlags.generatedHypothesesViewer}
                  onChange={(event) =>
                    setMaxGeneratedHypotheses(
                      Math.min(maxGeneratedHypothesisLimit, Math.max(0, Number.parseInt(event.target.value || "0", 10))),
                    )
                  }
                  className="mt-2 w-32 rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550 disabled:bg-slatewash-100 disabled:text-ink-500"
                />
              </label>

              {productFeatureFlags.exportsPlaceholder ? (
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    name="export-result-bundle"
                    checked
                    disabled
                    readOnly
                    className="mt-1 rounded border-slatewash-300 text-teal-550 focus:ring-teal-550"
                  />
                  <span>
                    <span className="block text-sm font-semibold text-ink-950">Prepare result bundle export</span>
                    <span className="mt-1 block text-sm leading-6 text-ink-600">
                      Stores an inline product-safe result bundle artifact after the run completes.
                    </span>
                  </span>
                </label>
              ) : (
                <p className="text-sm leading-6 text-ink-600">
                  Result bundle export placeholders are hidden by the current product feature flag.
                </p>
              )}
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
                type="submit"
                disabled={!acknowledged || usageBlocked || status === "submitting"}
                className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border border-teal-550 bg-teal-550 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:border-slatewash-200 disabled:bg-slatewash-200 disabled:text-ink-500"
              >
                <FlaskConical className="h-4 w-4" aria-hidden="true" />
                <span>{status === "submitting" || status === "redirecting" ? "Starting run" : "Start discovery run"}</span>
              </button>
            </div>

            {message ? (
              <div className={`rounded-product border p-3 text-sm leading-6 ${
                status === "error" || usageBlocked
                  ? "border-rose-200 bg-rose-50 text-ink-700"
                  : "border-teal-450/40 bg-teal-450/10 text-ink-700"
              }`}>
                {message}
              </div>
            ) : null}
          </form>
        </CardBody>
      </Card>

      <div className="grid content-start gap-6">
        <UsageEstimateCard
          generatedCount={generatedCount}
          taskUsageLabel={taskUsageLabel}
          usageLimit={usageLimit}
          usageRemaining={usageRemaining}
        />
        <RunDisclaimers />
        <ResearchUseBanner />
      </div>
    </div>
  );
}

function UsageEstimateCard({
  generatedCount,
  taskUsageLabel,
  usageLimit,
  usageRemaining,
}: {
  generatedCount: number;
  taskUsageLabel: string;
  usageLimit?: number | null;
  usageRemaining?: number | null;
}) {
  return (
    <Card>
      <CardHeader title="Usage estimate" eyebrow="Mock estimate" />
      <CardBody>
        <div className="grid gap-3">
          <EstimateRow icon={ListChecks} label="Discovery runs" value="1 discovery run" />
          <EstimateRow icon={CheckCircle2} label="Generated hypotheses" value={`${generatedCount} generated hypotheses`} />
          <EstimateRow icon={FileArchive} label="Estimated Codex task usage" value={taskUsageLabel} />
          <EstimateRow
            icon={ShieldAlert}
            label="Run limit remaining"
            value={usageLimit === null ? "Internal plan" : `${usageRemaining ?? 0} of ${usageLimit ?? 0}`}
          />
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
