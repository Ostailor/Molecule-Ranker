import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock3,
  FlaskConical,
  RotateCcw,
} from "lucide-react";
import type { DiscoveryRun } from "@/lib/mock-data";
import { compactNumber, dateLabel } from "@/lib/formatting";
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
  "Resolving disease/project context",
  "Retrieving source-backed candidates",
  "Reviewing evidence",
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
  cancelled: "Cancelled placeholder",
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
  run: DiscoveryRun;
  runState: DiscoveryRunViewState;
  projectName: string;
  resultHref: string;
}) {
  const steps = buildTimeline(run.startedAt, runState);
  const completionLabel = runState === "completed" ? "Ready" : runState === "partial" ? "Partial" : "Not ready";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-4">
        <Metric label="Run status" value={stateLabels[runState]} detail={projectName} icon={FlaskConical} />
        <Metric label="Candidate ranking" value={String(run.candidateCount)} detail="Research hypotheses" icon={CheckCircle2} />
        <Metric label="Evidence" value={compactNumber(run.evidenceCount)} detail="Review items" icon={AlertTriangle} />
        <Metric label="Started" value={dateLabel(run.startedAt)} detail={completionLabel} icon={Clock3} />
      </div>

      <Card>
        <CardHeader
          title={run.name}
          eyebrow="PLACEHOLDER_V0_1_RUN_STATUS"
          action={<StatusBadge tone={stateTones[runState]}>{stateLabels[runState]}</StatusBadge>}
        />
        <CardBody>
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
        </CardBody>
      </Card>

      <StateNotice runState={runState} resultHref={resultHref} />
    </div>
  );
}

function StateNotice({ runState, resultHref }: { runState: DiscoveryRunViewState; resultHref: string }) {
  if (runState === "completed") {
    return (
      <Card>
        <CardHeader title="Result bundle ready" eyebrow="Completed" action={<Button href={resultHref}>View result bundle</Button>} />
        <CardBody>
          <p className="text-sm leading-6 text-ink-600">
            Candidate ranking, evidence, generated hypotheses, limitations, and research notes are available for expert
            review.
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
            <p className="text-sm leading-6 text-ink-700">
              The mock workflow could not prepare a result bundle. Review the project objective, keep the request bounded
              to research planning, and start a new preview run.
            </p>
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
              Review partial bundle
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
              This placeholder state represents a stopped run. No result bundle is available from this mock state.
            </p>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="Partial result warning" eyebrow="Placeholder" />
      <CardBody>
        <p className="text-sm leading-6 text-ink-600">
          Result sections may appear gradually in a later release. This V0.1 screen does not execute a live workflow.
        </p>
      </CardBody>
    </Card>
  );
}

function buildTimeline(startedAt: string, runState: DiscoveryRunViewState): TimelineStep[] {
  const statusByState: Record<DiscoveryRunViewState, StepStatus[]> = {
    queued: ["Current", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting"],
    running: ["Complete", "Complete", "Complete", "Current", "Waiting", "Waiting", "Waiting"],
    completed: ["Complete", "Complete", "Complete", "Complete", "Complete", "Complete", "Complete"],
    failed: ["Complete", "Complete", "Complete", "Needs review", "Waiting", "Waiting", "Waiting"],
    partial: ["Complete", "Complete", "Complete", "Complete", "Needs review", "Needs review", "Waiting"],
    cancelled: ["Complete", "Stopped", "Waiting", "Waiting", "Waiting", "Waiting", "Waiting"],
  };

  return timelineLabels.map((label, index) => {
    const status = statusByState[runState][index];
    return {
      label,
      status,
      timestamp: status === "Waiting" ? "Pending" : dateLabel(addMinutes(startedAt, index * 4)),
    };
  });
}

function addMinutes(value: string, minutes: number) {
  const date = new Date(value);
  date.setMinutes(date.getMinutes() + minutes);
  return date.toISOString();
}
