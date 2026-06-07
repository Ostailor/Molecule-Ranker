import { clsx } from "clsx";
import { Badge, type BadgeTone } from "@/components/ui/badge";

export type StepTimelineItem = {
  label: string;
  status: string;
  timestamp?: string;
  tone?: BadgeTone;
};

export function StepTimeline({ steps }: { steps: StepTimelineItem[] }) {
  return (
    <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {steps.map((step, index) => (
        <li key={`${step.label}-${index}`} className="rounded-product border border-slatewash-200 bg-white p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Step {index + 1}</p>
          <p className="mt-2 text-sm font-semibold text-ink-950">{step.label}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge tone={step.tone ?? "gray"}>{step.status}</Badge>
            {step.timestamp ? <span className={clsx("text-xs text-ink-500")}>{step.timestamp}</span> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
