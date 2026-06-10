"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock3,
  FlaskConical,
  RotateCcw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { compactNumber, dateLabel } from "@/lib/formatting";
import type { ProductRun } from "@/lib/supabase/types";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";

export type DiscoveryRunViewState = "queued" | "running" | "completed" | "failed" | "partial" | "cancelled";

type StepStatus = "Complete" | "Current" | "Waiting" | "Needs review" | "Stopped";

type TimelineStep = {
  label: string;
  status: StepStatus;
  timestamp: string;
};

const timelineLabels = [
  "Queued",
  "Preparing product-safe run context",
  "Executing bounded discovery workflow",
  "Filtering product-safe artifacts",
  "Creating generated hypotheses if enabled",
  "Building result bundle",
  "Completed",
] as const;

const stateLabels: Record<DiscoveryRunViewState, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Needs attention",
  partial: "Partial success",
  cancelled: "Cancelled",
};

const stateTones: Record<DiscoveryRunViewState, "green" | "teal" | "amber" | "rose" | "gray"> = {
  queued: "gray",
  running: "teal",
  completed: "green",
  failed: "rose",
  partial: "amber",
  cancelled: "gray",
};

const stepTone: Record<StepStatus, "green" | "teal" | "amber" | "rose" | "gray"> = {
  Complete: "green",
  Current: "teal",
  Waiting: "gray",
  "Needs review": "amber",
  Stopped: "gray",
};

export function RunSummary({
  run,
  runState,
  projectName,
  resultHref,
}: {
  run: ProductRun;
  runState: DiscoveryRunViewState;
  projectName: string;
  resultHref: string;
}) {
  const [liveRun, setLiveRun] = useState(run);
  const [liveState, setLiveState] = useState(runState);
  const [accessMessage, setAccessMessage] = useState<string | null>(null);
  const [cancelMessage, setCancelMessage] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const isTerminal = isTerminalState(liveState);
  const isCancellable = liveState === "queued" || liveState === "running";
  const steps = useMemo(() => buildTimeline(liveRun, liveState), [liveRun, liveState]);
  const completionLabel = liveState === "completed" ? "Ready" : liveState === "partial" ? "Partial" : "Not ready";
  const summary =
    liveRun.result_summary && typeof liveRun.result_summary === "object" && !Array.isArray(liveRun.result_summary)
      ? (liveRun.result_summary as Record<string, unknown>)
      : {};
  const candidateCount = typeof summary.candidateCount === "number" ? summary.candidateCount : 0;
  const evidenceCount = typeof summary.evidenceItemCount === "number" ? summary.evidenceItemCount : 0;
  const generatedCount = typeof summary.generatedHypothesisCount === "number" ? summary.generatedHypothesisCount : 0;

  useEffect(() => {
    if (isTerminal) return undefined;

    let cancelled = false;

    async function pollStatus() {
      try {
        const response = await fetch(`/api/product/projects/${liveRun.project_id}/runs/${liveRun.id}/status`, {
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => null);

        if (!response.ok || !payload?.ok) {
          if (response.status === 401 || response.status === 403 || response.status === 404) {
            setAccessMessage(payload?.error?.message ?? "This run is not available in the current organization.");
            return;
          }

          setAccessMessage("Could not refresh run status.");
          return;
        }

        if (cancelled) return;

        const data = payload.data;
        setAccessMessage(null);
        setLiveRun((current) => ({
          ...current,
          status: data.status,
          progress: data.progress,
          error_summary: data.error_summary,
          result_summary: data.result_summary,
          started_at: data.started_at,
          completed_at: data.completed_at,
          updated_at: data.updated_at ?? current.updated_at,
        }));
        setLiveState(stateFromRunStatus(data.status));
      } catch {
        if (!cancelled) setAccessMessage("Could not refresh run status.");
      }
    }

    void pollStatus();
    const interval = window.setInterval(() => {
      void pollStatus();
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [isTerminal, liveRun.id, liveRun.project_id]);

  async function cancelRun() {
    if (!isCancellable || isCancelling) return;

    setIsCancelling(true);
    setCancelMessage(null);

    try {
      const response = await fetch(`/api/product/projects/${liveRun.project_id}/runs/${liveRun.id}/cancel`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok || !payload?.ok) {
        setCancelMessage(payload?.error?.message ?? "Could not cancel this run.");
        return;
      }

      setLiveRun(payload.data.run);
      setLiveState(stateFromRunStatus(payload.data.run.status));
      setCancelMessage(payload.data.cancellation?.message ?? "Run cancellation recorded.");
    } catch {
      setCancelMessage("Could not cancel this run.");
    } finally {
      setIsCancelling(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-4">
        <Metric label="Run status" value={stateLabels[liveState]} detail={projectName} icon={FlaskConical} />
        <Metric label="Candidate ranking" value={String(candidateCount)} detail="Summary only" icon={CheckCircle2} />
        <Metric label="Evidence" value={compactNumber(evidenceCount)} detail="Summary only" icon={AlertTriangle} />
        <Metric label="Started" value={dateLabel(liveRun.started_at ?? liveRun.created_at)} detail={completionLabel} icon={Clock3} />
      </div>

      <Card>
        <CardHeader
          title={liveRun.disease_or_goal}
          eyebrow="V0.3 product run status"
          action={<StatusBadge tone={stateTones[liveState]}>{stateLabels[liveState]}</StatusBadge>}
        />
        <CardBody>
          <div className="mb-5 grid gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3 text-sm leading-6 text-ink-700 md:grid-cols-2">
            <p>
              <span className="font-semibold text-ink-950">Run mode:</span> {runModeLabel(liveRun.mode)}
            </p>
            <p>
              <span className="font-semibold text-ink-950">Disease or goal:</span> {liveRun.disease_or_goal}
            </p>
            <p>
              <span className="font-semibold text-ink-950">Target focus:</span> {liveRun.target_focus || "Not specified"}
            </p>
            <p>
              <span className="font-semibold text-ink-950">Started:</span> {dateLabel(liveRun.started_at ?? liveRun.created_at)}
            </p>
            <p>
              <span className="font-semibold text-ink-950">Completed:</span>{" "}
              {liveRun.completed_at ? dateLabel(liveRun.completed_at) : "Pending"}
            </p>
          </div>

          <div className="grid gap-3 lg:grid-cols-7">
            {steps.map((step, index) => (
              <div key={step.label} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Step {index + 1}</p>
                <p className="mt-2 min-h-12 text-sm font-semibold leading-5 text-ink-950">{step.label}</p>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <StatusBadge tone={stepTone[step.status]}>{step.status}</StatusBadge>
                  <span className="text-xs text-ink-500">{step.timestamp}</span>
                </div>
              </div>
            ))}
          </div>

          {accessMessage ? <SafeMessage tone="error">{accessMessage}</SafeMessage> : null}
          {cancelMessage ? <SafeMessage tone={liveState === "cancelled" ? "info" : "error"}>{cancelMessage}</SafeMessage> : null}

          {isCancellable ? (
            <div className="mt-5">
              <button
                type="button"
                onClick={cancelRun}
                disabled={isCancelling}
                className="focus-ring inline-flex h-9 items-center justify-center rounded-product border border-rose-300 bg-white px-3 text-sm font-semibold text-rose-700 transition hover:border-rose-400 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isCancelling ? "Cancelling" : "Cancel run"}
              </button>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <SafeWarnings generatedCount={generatedCount} />
      <StateNotice runState={liveState} resultHref={resultHref} errorSummary={liveRun.error_summary} />
    </div>
  );
}

function SafeWarnings({ generatedCount }: { generatedCount: number }) {
  return (
    <Card>
      <CardHeader title="Safety boundaries" eyebrow="Product-safe status" />
      <CardBody>
        <ul className="grid gap-2 text-sm leading-6 text-ink-700 md:grid-cols-2">
          {[
            "No patient-specific information.",
            "Not medical advice.",
            "Not a lab protocol.",
            "No synthesis instructions.",
            "No dosing guidance.",
            generatedCount > 0
              ? "Generated hypotheses are computational only."
              : "Generated hypotheses are disabled or absent for this run.",
          ].map((item) => (
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

function StateNotice({
  runState,
  resultHref,
  errorSummary,
}: {
  runState: DiscoveryRunViewState;
  resultHref: string;
  errorSummary: string | null;
}) {
  if (runState === "completed") {
    return (
      <Card>
        <CardHeader title="Result bundle ready" eyebrow="Completed" action={<Button href={resultHref}>View result bundle</Button>} />
        <CardBody>
          <p className="text-sm leading-6 text-ink-600">
            A product-safe result bundle summary is available for expert review. Internal execution output is hidden.
          </p>
        </CardBody>
      </Card>
    );
  }

  if (runState === "failed") {
    return (
      <Card>
        <CardHeader title="Run needs attention" eyebrow="Review required" />
        <CardBody>
          <div className="flex items-start gap-3 rounded-product border border-rose-200 bg-rose-50 p-3">
            <RotateCcw className="mt-0.5 h-4 w-4 shrink-0 text-rose-700" aria-hidden="true" />
            <div className="text-sm leading-6 text-ink-700">
              <p>{errorSummary || "The bounded workflow could not prepare a product-safe result bundle."}</p>
              <p className="mt-2">Review the project objective, keep the request bounded to research planning, and start a new preview run.</p>
            </div>
          </div>
        </CardBody>
      </Card>
    );
  }

  if (runState === "partial") {
    return (
      <Card>
        <CardHeader
          title="Partial result warning"
          eyebrow="Placeholder"
          action={
            <Button href={resultHref} variant="secondary">
              View result bundle
            </Button>
          }
        />
        <CardBody>
          <p className="text-sm leading-6 text-ink-700">
            Some candidate ranking and evidence sections are available, but generated hypotheses or export packaging may
            require reviewer follow-up before use in planning.
          </p>
        </CardBody>
      </Card>
    );
  }

  if (runState === "cancelled") {
    return (
      <Card>
        <CardHeader title="Run cancelled" eyebrow="Placeholder" />
        <CardBody>
          <div className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <Ban className="mt-0.5 h-4 w-4 shrink-0 text-ink-500" aria-hidden="true" />
            <p className="text-sm leading-6 text-ink-600">
              This run was marked cancelled. No result bundle is available from this run state.
            </p>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="Result bundle pending" eyebrow="In progress" />
      <CardBody>
        <p className="text-sm leading-6 text-ink-600">
          Result sections are written after the product-safe runner completes. This page refreshes status automatically
          while the run is queued or running.
        </p>
      </CardBody>
    </Card>
  );
}

function buildTimeline(run: ProductRun, runState: DiscoveryRunViewState): TimelineStep[] {
  const statusByState: Record<DiscoveryRunViewState, StepStatus[]> = {
    queued: ["Current", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting"],
    running: ["Complete", "Complete", "Current", "Waiting", "Waiting", "Waiting", "Waiting"],
    completed: ["Complete", "Complete", "Complete", "Complete", "Complete", "Complete", "Complete"],
    failed: ["Complete", "Complete", "Needs review", "Waiting", "Waiting", "Waiting", "Waiting"],
    partial: ["Complete", "Complete", "Complete", "Complete", "Needs review", "Complete", "Complete"],
    cancelled: ["Complete", "Stopped", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting"],
  };
  const progress = progressRecord(run.progress);
  const currentStep = typeof progress.step === "string" ? progress.step : null;
  const message = typeof progress.message === "string" ? progress.message : null;
  const startedAt = run.started_at ?? run.created_at;

  return timelineLabels.map((label, index) => {
    let status = statusByState[runState][index];
    if (runState === "running" && currentStep === "completed") status = "Complete";

    return {
      label: index === 2 && message ? `${label}: ${message}` : label,
      status,
      timestamp: stepTimestamp({ status, index, startedAt, run }),
    };
  });
}

function progressRecord(value: ProductRun["progress"]) {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stepTimestamp({
  status,
  index,
  startedAt,
  run,
}: {
  status: StepStatus;
  index: number;
  startedAt: string;
  run: ProductRun;
}) {
  if (status === "Waiting") return "Pending";
  if (index === 6 && run.completed_at) return dateLabel(run.completed_at);

  return dateLabel(addMinutes(startedAt, index * 4));
}

function addMinutes(value: string, minutes: number) {
  const date = new Date(value);
  date.setMinutes(date.getMinutes() + minutes);
  return date.toISOString();
}

function isTerminalState(runState: DiscoveryRunViewState) {
  return runState === "completed" || runState === "failed" || runState === "partial" || runState === "cancelled";
}

function stateFromRunStatus(status: string | undefined | null): DiscoveryRunViewState {
  if (status === "succeeded") return "completed";
  if (status === "partially_succeeded") return "partial";
  if (status === "running") return "running";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "queued";
}

function runModeLabel(mode: string) {
  if (mode === "mocked") return "Mocked";
  if (mode === "read_only_live") return "Read-only live";
  return "Dry run";
}

function SafeMessage({ tone, children }: { tone: "error" | "info"; children: string }) {
  const className =
    tone === "error"
      ? "mt-4 rounded-product border border-rose-200 bg-rose-50 p-3 text-sm leading-6 text-ink-700"
      : "mt-4 rounded-product border border-teal-450/40 bg-teal-450/10 p-3 text-sm leading-6 text-ink-700";

  return <div className={className}>{children}</div>;
}
