import {
  AlertTriangle,
  Boxes,
  ClipboardCheck,
  Download,
  FileArchive,
  FileText,
  FlaskConical,
  Lightbulb,
  ShieldAlert,
} from "lucide-react";
import type { DiscoveryRun, Project, ResultBundle } from "@/lib/mock-data";
import { candidates, evidenceItems, generatedHypotheses } from "@/lib/mock-data";
import { dateLabel } from "@/lib/formatting";
import { productFeatureFlags } from "@/lib/product/feature-flags";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";

type ResultBundleOverviewProps = {
  project: Project;
  run: DiscoveryRun;
  bundle?: ResultBundle;
  projectId: string;
  runId: string;
};

const keyLimitations = [
  "All rows are synthetic UI demo data and must be replaced with imported evidence before research use.",
  "Candidate prioritization scores are placeholders for workflow review, not a measure of biomedical readiness.",
  "Evidence coverage may be incomplete and requires expert review before downstream planning.",
  "Generated hypotheses are not source-backed unless exact imported results are attached in a later release.",
];

const guardrailNotices = [
  "Result bundle is a research-planning artifact.",
  "Not clinical validation.",
  "Not medical advice.",
  "Not a lab protocol.",
  "Not a synthesis plan.",
  "Generated hypotheses have no direct evidence unless exact imported results exist.",
];

const reviewChecklist = [
  "Confirm project objective and disease or area are bounded for research planning.",
  "Review candidate ranking warnings before saving candidates.",
  "Check evidence coverage and provenance notes for each candidate.",
  "Separate generated hypotheses from imported evidence during review.",
  "Record limitations and unresolved questions in research notes before export.",
];

export function ResultBundleOverview({
  project,
  run,
  bundle,
  projectId,
  runId,
}: ResultBundleOverviewProps) {
  const warningsCount = candidates.reduce((total, candidate) => total + candidate.warnings.length, 0) +
    generatedHypotheses.reduce((total, hypothesis) => total + hypothesis.warnings.length, 0);
  const exportAvailability = bundle?.status === "Ready for review" ? "Available" : "Draft";
  const candidatesHref = `/projects/${projectId}/runs/${runId}/candidates`;
  const evidenceHref = `/projects/${projectId}/runs/${runId}/evidence`;
  const generatedHref = `/projects/${projectId}/runs/${runId}/generated`;
  const generatedHypothesesCount = productFeatureFlags.generatedHypothesesViewer ? generatedHypotheses.length : 0;
  const resultSections = bundle?.sections ?? [
    "Candidate ranking",
    "Evidence",
    ...(productFeatureFlags.generatedHypothesesViewer ? ["Generated hypotheses"] : []),
    "Limitations",
  ];

  return (
    <div className="space-y-6">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Metric label="Ranked candidates" value={String(candidates.length)} detail="Candidate ranking" icon={Boxes} />
        <Metric label="Evidence items" value={String(evidenceItems.length)} detail="Evidence coverage" icon={FileText} />
        <Metric
          label="Generated hypotheses"
          value={String(generatedHypothesesCount)}
          detail={productFeatureFlags.generatedHypothesesViewer ? "No direct evidence" : "Feature hidden"}
          icon={Lightbulb}
        />
        <Metric label="Warnings" value={String(warningsCount)} detail="Require review" icon={AlertTriangle} />
        <Metric label="Export availability" value={exportAvailability} detail="Placeholder" icon={Download} />
      </section>

      <Card>
        <CardHeader
          title="Result summary"
          eyebrow="PLACEHOLDER_V0_1_RESULT_OVERVIEW"
          action={<StatusBadge tone={bundle?.status === "Ready for review" ? "green" : "amber"}>{bundle?.status ?? "Draft"}</StatusBadge>}
        />
        <CardBody>
          <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
            <div>
              <p className="text-sm leading-6 text-ink-700">
                {bundle?.name ?? "Synthetic result bundle"} summarizes {project.name} outputs from {run.name}. It is
                organized for human review across candidate ranking, evidence, generated hypotheses, limitations, and
                research notes.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {resultSections.map((section) => (
                  <StatusBadge key={section} tone="gray">
                    {section}
                  </StatusBadge>
                ))}
              </div>
            </div>
            <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Run context</p>
              <dl className="mt-3 grid gap-3 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Project</dt>
                  <dd className="text-right font-semibold text-ink-950">{project.therapeuticArea}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Run mode</dt>
                  <dd className="text-right font-semibold text-ink-950">{run.mode}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Updated</dt>
                  <dd className="text-right font-semibold text-ink-950">
                    {bundle?.exportedAt ? dateLabel(bundle.exportedAt) : dateLabel(run.startedAt)}
                  </dd>
                </div>
              </dl>
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-6 xl:grid-cols-3">
        <SummaryCard
          title="Candidate ranking summary"
          eyebrow="Ranked candidates"
          icon={Boxes}
          href={candidatesHref}
          cta="Open candidates"
          body="Candidate hypotheses are ordered for research review with confidence labels, evidence counts, tags, and warnings."
        />
        <SummaryCard
          title="Evidence coverage"
          eyebrow="Synthetic rows"
          icon={FileText}
          href={evidenceHref}
          cta="Open evidence"
          body="Evidence items summarize source type, title, confidence, and provenance notes for UI review only."
        />
        {productFeatureFlags.generatedHypothesesViewer ? (
          <SummaryCard
            title="Generated hypotheses summary"
            eyebrow="No direct evidence"
            icon={FlaskConical}
            href={generatedHref}
            cta="Open generated hypotheses"
            body="Generated hypotheses are separated from evidence-backed sections and require explicit human review."
          />
        ) : null}
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <ListCard title="Key limitations" eyebrow="Review required" items={keyLimitations} icon={AlertTriangle} />
        <ListCard title="Guardrail notices" eyebrow="Research boundary" items={guardrailNotices} icon={ShieldAlert} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        {productFeatureFlags.exportsPlaceholder ? (
          <Card id="export-actions">
            <CardHeader title="Export actions placeholder" eyebrow="PLACEHOLDER_V0_1_EXPORT" />
            <CardBody>
              <p className="text-sm leading-6 text-ink-600">
                Export actions are disabled in V0.2. This area reserves the future download, share, and archive controls
                without creating files or calling a backend.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                <Button href="#export-actions" icon={FileArchive} variant="secondary">
                  Export placeholder
                </Button>
                <Button href={candidatesHref} variant="ghost">
                  Review before export
                </Button>
              </div>
            </CardBody>
          </Card>
        ) : (
          <Card id="export-actions">
            <CardHeader title="Export actions hidden" eyebrow="Feature disabled" />
            <CardBody>
              <p className="text-sm leading-6 text-ink-600">
                Export placeholders are hidden by the current product feature flag. No export files are created in V0.2.
              </p>
            </CardBody>
          </Card>
        )}

        <ListCard
          title="Human review checklist"
          eyebrow="Before use"
          items={reviewChecklist}
          icon={ClipboardCheck}
        />
      </div>
    </div>
  );
}

function SummaryCard({
  title,
  eyebrow,
  icon: Icon,
  body,
  href,
  cta,
}: {
  title: string;
  eyebrow: string;
  icon: typeof Boxes;
  body: string;
  href: string;
  cta: string;
}) {
  return (
    <Card>
      <CardHeader title={title} eyebrow={eyebrow} />
      <CardBody>
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <p className="text-sm leading-6 text-ink-600">{body}</p>
        <Button href={href} variant="secondary" className="mt-4">
          {cta}
        </Button>
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
