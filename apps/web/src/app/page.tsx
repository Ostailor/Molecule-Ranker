import {
  ArrowRight,
  BookOpenCheck,
  CheckCircle2,
  Download,
  FileArchive,
  FlaskConical,
  Microscope,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { MoleculeGlyph } from "@/components/product/molecule-glyph";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";

const workflowSteps = [
  {
    title: "Create project",
    body: "Define the research objective, evidence scope, and review expectations for a molecule ranking workspace.",
    icon: BookOpenCheck,
  },
  {
    title: "Run discovery",
    body: "Use mock discovery runs to assemble public evidence, molecule records, literature signals, and checks.",
    icon: FlaskConical,
  },
  {
    title: "Review candidates",
    body: "Compare candidate ranking, evidence, generated hypotheses, and research notes before any next-step decision.",
    icon: Microscope,
  },
  {
    title: "Export result bundle",
    body: "Package hypotheses, evidence, provenance, limitations, and review notes into an auditable result bundle.",
    icon: Download,
  },
];

const outcomes = [
  "Ranked candidate hypotheses",
  "Evidence and provenance",
  "Generated hypothesis section",
  "Limitations and guardrails",
  "Exportable result bundle",
];

const notClaims = [
  "Not medical advice",
  "Not clinical decision support",
  "Not a cure finder",
  "Not a synthesis planner",
  "Not a lab protocol generator",
  "Not a regulated medical product",
];

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-white">
      <header className="border-b border-slatewash-200">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Logo />
          <nav className="hidden items-center gap-6 text-sm font-medium text-ink-600 md:flex" aria-label="Landing">
            <a href="#workflow" className="hover:text-ink-950">
              Workflow
            </a>
            <a href="#outcomes" className="hover:text-ink-950">
              What you get
            </a>
            <a href="#not" className="hover:text-ink-950">
              What this is not
            </a>
            <a href="#access" className="hover:text-ink-950">
              Pilot access
            </a>
          </nav>
          <Button href="/login" variant="secondary">
            Log in
          </Button>
        </div>
      </header>

      <section className="molecule-grid border-b border-slatewash-200 bg-slatewash-50">
        <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-7xl items-center gap-10 px-4 py-12 sm:px-6 lg:grid-cols-[0.95fr_1.05fr] lg:px-8">
          <div>
            <h1 className="max-w-3xl text-4xl font-semibold tracking-normal text-ink-950 sm:text-6xl">
              Evidence-backed molecule ranking for research hypothesis generation.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-ink-600 sm:text-lg">
              Create auditable research-planning result bundles from public biomedical evidence, molecule databases,
              literature, generated hypotheses, and developability checks.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button href="/feedback" icon={ArrowRight}>
                Request pilot access
              </Button>
              <Button href="/login" variant="secondary">
                Log in
              </Button>
            </div>
            <div className="mt-8 max-w-2xl">
              <ResearchUseBanner compact />
            </div>
          </div>
          <Card className="shadow-soft">
            <CardBody className="p-4 sm:p-5">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-product border border-slatewash-200 bg-white p-3">
                  <FlaskConical className="h-5 w-5 text-teal-700" aria-hidden="true" />
                  <p className="mt-3 text-sm font-semibold text-ink-950">4 candidates</p>
                  <p className="mt-1 text-xs text-ink-600">Candidate ranking</p>
                </div>
                <div className="rounded-product border border-slatewash-200 bg-white p-3">
                  <ShieldCheck className="h-5 w-5 text-teal-700" aria-hidden="true" />
                  <p className="mt-3 text-sm font-semibold text-ink-950">4 demo rows</p>
                  <p className="mt-1 text-xs text-ink-600">Evidence trace</p>
                </div>
                <div className="rounded-product border border-slatewash-200 bg-white p-3">
                  <Sparkles className="h-5 w-5 text-teal-700" aria-hidden="true" />
                  <p className="mt-3 text-sm font-semibold text-ink-950">3 hypotheses</p>
                  <p className="mt-1 text-xs text-ink-600">Generated section</p>
                </div>
              </div>
              <div className="mt-4 grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
                <MoleculeGlyph label="ExampleCandidateA" />
                <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Result bundle preview</p>
                  <h2 className="mt-2 text-xl font-semibold text-ink-950">ExampleDiseaseA discovery run</h2>
                  <div className="mt-4 space-y-3">
                    {["Evidence and provenance", "Developability watch band", "Limitations and guardrails"].map((item) => (
                      <div key={item} className="flex items-center justify-between rounded-product bg-white px-3 py-2">
                        <span className="text-sm text-ink-700">{item}</span>
                        <span className="h-2 w-16 rounded-full bg-teal-450/30">
                          <span className="block h-2 w-2/3 rounded-full bg-teal-450" />
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </CardBody>
          </Card>
        </div>
      </section>

      <section id="workflow" className="border-b border-slatewash-200 bg-white py-14">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-semibold text-ink-950">Workflow preview</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              A focused path from research question to review-ready result bundle.
            </p>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {workflowSteps.map((step, index) => (
              <div key={step.title} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">
                    Step {index + 1}
                  </span>
                  <span className="flex h-8 w-8 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
                    <step.icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                </div>
                <h3 className="mt-4 text-lg font-semibold text-ink-950">{step.title}</h3>
                <p className="mt-2 text-sm leading-6 text-ink-600">{step.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="outcomes" className="border-b border-slatewash-200 bg-slatewash-50 py-14">
        <div className="mx-auto grid max-w-7xl gap-8 px-4 sm:px-6 lg:grid-cols-[0.8fr_1.2fr] lg:px-8">
          <div>
            <h2 className="text-2xl font-semibold text-ink-950">What you get</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              The product is organized around auditable research-planning artifacts, not clinical conclusions.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {outcomes.map((item) => (
              <div key={item} className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-white p-4">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-teal-700" aria-hidden="true" />
                <p className="text-sm font-semibold text-ink-950">{item}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="not" className="border-b border-slatewash-200 bg-white py-14">
        <div className="mx-auto grid max-w-7xl gap-8 px-4 sm:px-6 lg:grid-cols-[0.8fr_1.2fr] lg:px-8">
          <div>
            <h2 className="text-2xl font-semibold text-ink-950">What this is not</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              MolCreate is scoped to research planning and review workflows.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {notClaims.map((item) => (
              <div key={item} className="rounded-product border border-amber-450/25 bg-amber-450/10 p-4">
                <p className="text-sm font-semibold text-ink-950">{item}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="access" className="bg-slatewash-50 py-14">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <Card className="overflow-hidden">
            <CardBody className="grid gap-6 p-6 sm:p-8 lg:grid-cols-[1fr_auto] lg:items-center">
              <div>
                <div className="flex h-10 w-10 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
                  <FileArchive className="h-5 w-5" aria-hidden="true" />
                </div>
                <h2 className="mt-4 text-2xl font-semibold text-ink-950">Pilot access</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
                  Explore the mock research workflow, review result bundle structure, and share feedback before any
                  production integration.
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button href="/feedback" icon={ArrowRight}>
                  Request pilot access
                </Button>
                <Button href="/login" variant="secondary">
                  Log in
                </Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </section>

      <section className="bg-white py-8">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <ResearchUseBanner />
        </div>
      </section>
    </main>
  );
}
