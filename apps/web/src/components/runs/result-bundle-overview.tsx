"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  ClipboardCheck,
  FileArchive,
  FileText,
  FlaskConical,
  Lightbulb,
  ShieldAlert,
} from "lucide-react";

import { dateLabel } from "@/lib/formatting";
import { productFeatureFlags } from "@/lib/product/feature-flags";
import type { ProductRun, ProductRunArtifact, Project } from "@/lib/supabase/types";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";

type ResultBundleOverviewProps = {
  project: Project;
  initialRun: ProductRun;
  projectId: string;
  runId: string;
};

type ResultBundleApiPayload = {
  run?: Partial<ProductRun> & {
    status?: string;
    error_summary?: string | null;
    result_summary?: unknown;
  };
  artifact?: ProductRunArtifact | null;
  artifacts?: ProductRunArtifact[];
  summary?: unknown;
};

const keyLimitations = [
  "The V0.3 result is a bounded product-safe summary, not an advanced result bundle viewer.",
  "Deep candidate and evidence viewers remain a V0.4 scope item.",
  "Evidence coverage may be incomplete and requires expert review before downstream planning.",
  "Generated hypotheses are computational only and require separate human review.",
];

const guardrailNotices = [
  "Result bundle is a research-planning artifact.",
  "Not clinical validation.",
  "Not medical advice.",
  "Not a lab protocol.",
  "Not a synthesis plan.",
  "No patient-specific treatment guidance.",
];

const reviewChecklist = [
  "Confirm project objective and disease or goal are bounded for research planning.",
  "Review summary counts and warnings before using downstream planning notes.",
  "Check evidence coverage limitations before prioritizing follow-up work.",
  "Separate generated hypotheses from evidence-backed sections during review.",
  "Record unresolved questions before any export or handoff.",
];

export function ResultBundleOverview({ project, initialRun, projectId, runId }: ResultBundleOverviewProps) {
  const [run, setRun] = useState(initialRun);
  const [bundle, setBundle] = useState<ProductRunArtifact | null>(null);
  const [artifacts, setArtifacts] = useState<ProductRunArtifact[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadResultBundle() {
      try {
        const response = await fetch(`/api/product/projects/${projectId}/runs/${runId}/result-bundle`, {
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => null);

        if (!response.ok || !payload?.ok) {
          if (response.status === 401 || response.status === 403 || response.status === 404) {
            setMessage(payload?.error?.message ?? "This result bundle is not available in the current organization.");
            setLoaded(true);
            return;
          }

          setMessage("Could not load the product-safe result bundle.");
          setLoaded(true);
          return;
        }

        if (cancelled) return;

        const data = payload.data as ResultBundleApiPayload;
        setRun((current) => ({ ...current, ...data.run }));
        setBundle(data.artifact ?? null);
        setArtifacts(data.artifacts ?? []);
        setMessage(null);
        setLoaded(true);
      } catch {
        if (!cancelled) {
          setMessage("Could not load the product-safe result bundle.");
          setLoaded(true);
        }
      }
    }

    void loadResultBundle();

    return () => {
      cancelled = true;
    };
  }, [projectId, runId]);

  const bundleContent = objectValue(bundle?.content_json);
  const metadata = objectValue(bundle?.metadata);
  const artifactSummary = objectValue(bundleContent.summary);
  const payload = objectValue(bundleContent.payload);
  const payloadCounts = objectValue(payload.counts);
  const runSummary = objectValue(run.result_summary);
  const summary = { ...runSummary, ...artifactSummary };
  const counts = {
    candidates: numberValue(summary.candidateCount, payloadCounts.ranked_candidates),
    evidence: numberValue(summary.evidenceItemCount, payloadCounts.evidence_items),
    generated: productFeatureFlags.generatedHypothesesViewer
      ? numberValue(summary.generatedHypothesisCount, payloadCounts.generated_hypotheses)
      : 0,
    warnings: numberValue(summary.warningCount, payloadCounts.warnings),
  };
  const sections = stringArrayValue(summary.sections, [
    "Candidate summary",
    "Evidence summary",
    ...(productFeatureFlags.generatedHypothesesViewer ? ["Generated summary"] : []),
    "Limitations",
    "Required human review",
  ]);
  const displayName = typeof metadata.display_name === "string" ? metadata.display_name : "Product-safe result bundle";
  const isFailed = run.status === "failed";
  const isPartial = run.status === "partially_succeeded";
  const isPending = !isFailed && !bundle;

  if (message) {
    return <SafeResultState title="Result bundle unavailable" tone="rose" message={message} />;
  }

  if (isFailed) {
    return (
      <SafeResultState
        title="Run failed before result bundle creation"
        tone="rose"
        message={run.error_summary || "The bounded workflow could not prepare a product-safe result bundle."}
      />
    );
  }

  if (isPending) {
    return (
      <SafeResultState
        title={loaded ? "Result bundle pending" : "Loading result bundle"}
        tone="amber"
        message="The product-safe result bundle is not available yet. Check the run status page while the workflow completes."
      />
    );
  }

  return (
    <div className="space-y-6">
      {isPartial ? (
        <Card>
          <CardHeader title="Partial result warning" eyebrow="Review required" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-700">
              This run partially succeeded. Review available summaries and limitations before using the bundle for
              research planning.
            </p>
          </CardBody>
        </Card>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Metric label="Ranked candidates" value={String(counts.candidates)} detail="Summary only" icon={Boxes} />
        <Metric label="Evidence items" value={String(counts.evidence)} detail="Summary only" icon={FileText} />
        <Metric
          label="Generated hypotheses"
          value={String(counts.generated)}
          detail={productFeatureFlags.generatedHypothesesViewer ? "Computational only" : "Feature hidden"}
          icon={Lightbulb}
        />
        <Metric label="Warnings" value={String(counts.warnings)} detail="Require review" icon={AlertTriangle} />
        <Metric label="Artifacts" value={String(artifacts.length)} detail="Product-safe list" icon={FileArchive} />
      </section>

      <Card>
        <CardHeader
          title="Result summary"
          eyebrow="V0.3 product-safe artifact"
          action={<StatusBadge tone={isPartial ? "amber" : "green"}>{String(summary.status ?? "Ready for review")}</StatusBadge>}
        />
        <CardBody>
          <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
            <div>
              <p className="text-sm leading-6 text-ink-700">
                {displayName} summarizes {project.name} outputs from {run.disease_or_goal}. It is a summary-level
                artifact for human review and does not include deep candidate or evidence viewers.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {sections.map((section) => (
                  <StatusBadge key={section} tone="gray">
                    {section}
                  </StatusBadge>
                ))}
              </div>
            </div>
            <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Run context</p>
              <dl className="mt-3 grid gap-3 text-sm">
                <SummaryRow label="Project" value={project.disease_focus ?? "Research area"} />
                <SummaryRow label="Run mode" value={run.mode} />
                <SummaryRow label="Updated" value={dateLabel(bundle?.created_at ?? run.completed_at ?? run.created_at)} />
              </dl>
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-6 xl:grid-cols-3">
        <SummaryCard
          title="Candidate summary"
          eyebrow="Summary only"
          icon={Boxes}
          body={`The result bundle reports ${counts.candidates} ranked candidate summaries. Deep candidate inspection remains V0.4 scope.`}
        />
        <SummaryCard
          title="Evidence summary"
          eyebrow="Summary only"
          icon={FileText}
          body={`The result bundle reports ${counts.evidence} evidence summary items. Deep evidence review remains V0.4 scope.`}
        />
        <SummaryCard
          title="Generated summary"
          eyebrow="Computational only"
          icon={FlaskConical}
          body={`The result bundle reports ${counts.generated} generated hypotheses. Generated hypotheses require human review and are not direct evidence.`}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <ListCard title="Limitations" eyebrow="Review required" items={keyLimitations} icon={AlertTriangle} />
        <ListCard title="Required human review" eyebrow="Before use" items={reviewChecklist} icon={ClipboardCheck} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <ListCard title="Guardrail notices" eyebrow="Research boundary" items={guardrailNotices} icon={ShieldAlert} />
        <ArtifactList artifacts={artifacts} />
      </div>
    </div>
  );
}

function SafeResultState({ title, message, tone }: { title: string; message: string; tone: "amber" | "rose" }) {
  return (
    <Card>
      <CardHeader title={title} eyebrow={tone === "rose" ? "Safe failure state" : "Pending state"} />
      <CardBody>
        <div
          className={`rounded-product border p-3 text-sm leading-6 text-ink-700 ${
            tone === "rose" ? "border-rose-200 bg-rose-50" : "border-amber-200 bg-amber-50"
          }`}
        >
          {message}
        </div>
      </CardBody>
    </Card>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-ink-500">{label}</dt>
      <dd className="text-right font-semibold text-ink-950">{value}</dd>
    </div>
  );
}

function SummaryCard({
  title,
  eyebrow,
  icon: Icon,
  body,
}: {
  title: string;
  eyebrow: string;
  icon: typeof Boxes;
  body: string;
}) {
  return (
    <Card>
      <CardHeader title={title} eyebrow={eyebrow} />
      <CardBody>
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <p className="text-sm leading-6 text-ink-600">{body}</p>
      </CardBody>
    </Card>
  );
}

function ListCard({
  title,
  eyebrow,
  items,
  icon: Icon,
}: {
  title: string;
  eyebrow: string;
  items: string[];
  icon: typeof AlertTriangle;
}) {
  return (
    <Card>
      <CardHeader title={title} eyebrow={eyebrow} />
      <CardBody>
        <ul className="grid gap-3 text-sm leading-6 text-ink-700">
          {items.map((item) => (
            <li key={item} className="flex gap-3">
              <span className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slatewash-100 text-teal-700">
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

function ArtifactList({ artifacts }: { artifacts: ProductRunArtifact[] }) {
  return (
    <Card>
      <CardHeader title="Artifact list" eyebrow="Product-safe artifacts" />
      <CardBody>
        {artifacts.length === 0 ? (
          <p className="text-sm leading-6 text-ink-600">No product-safe artifacts are available yet.</p>
        ) : (
          <ul className="grid gap-3 text-sm leading-6 text-ink-700">
            {artifacts.map((artifact) => (
              <li key={artifact.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="font-semibold text-ink-950">{artifact.artifact_type}</span>
                  <StatusBadge tone={artifact.admin_only ? "amber" : "green"}>
                    {artifact.admin_only ? "Admin only" : "Visible"}
                  </StatusBadge>
                </div>
                <p className="mt-1 text-xs text-ink-500">
                  {artifact.storage_kind} · {artifact.size_bytes ?? 0} bytes · {dateLabel(artifact.created_at)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function numberValue(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }

  return 0;
}

function stringArrayValue(value: unknown, fallback: string[]) {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : fallback;
}
