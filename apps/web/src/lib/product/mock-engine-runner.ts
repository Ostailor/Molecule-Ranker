import { createHash } from "node:crypto";

import type { EngineArtifact, ProductArtifactSummary } from "./artifact-filter";
import type { ProductSafeResultBundle } from "./engine-runner";
import type { ProductRunSafeOptions } from "./run-safety";
import type { ProductRun, Project } from "@/lib/supabase/types";

const syntheticMarker = {
  synthetic: true,
  for_ui_test_only: true,
} as const;

const exampleDisease = "ExampleDiseaseA";
const exampleTarget = "ExampleTargetA";
const exampleCandidate = "ExampleCandidateA";

const guardrails = [
  "Synthetic UI-test artifact only.",
  "Research-planning artifact only.",
  "Not biomedical evidence.",
  "Not medical advice.",
  "Not clinical validation.",
  "Not a lab protocol.",
  "Not a synthesis plan.",
  "Generated hypotheses are computational only.",
  "No dose-related guidance.",
];

const limitations = [
  "This deterministic V0.3 mock runner does not perform scientific discovery.",
  "All names use ExampleDiseaseA, ExampleTargetA, and ExampleCandidateA placeholders.",
  "Generated hypotheses are computational only and use UI-test placeholders with direct_evidence=false.",
  "Human review is required before using any real product-safe engine output.",
];

function generatedHypothesisCount(options: ProductRunSafeOptions) {
  if (!options.includeGeneratedHypotheses) return 0;

  return Math.min(Math.max(0, Math.floor(options.maxGeneratedHypotheses ?? 0)), 3);
}

function artifactFromJson({
  artifactType,
  fileName,
  contentJson,
  publicToUser = true,
  adminOnly = false,
}: {
  artifactType: EngineArtifact["artifactType"];
  fileName: string;
  contentJson: Record<string, unknown>;
  publicToUser?: boolean;
  adminOnly?: boolean;
}): EngineArtifact {
  const contentText = JSON.stringify(contentJson, null, 2);

  return {
    artifactType,
    path: fileName,
    contentText,
    contentJson,
    sha256: createHash("sha256").update(contentText).digest("hex"),
    sizeBytes: new TextEncoder().encode(contentText).byteLength,
    publicToUser,
    adminOnly,
    metadata: {
      file_name: fileName,
      ...syntheticMarker,
    },
  };
}

function artifactFromMarkdown({
  artifactType,
  fileName,
  contentText,
}: {
  artifactType: EngineArtifact["artifactType"];
  fileName: string;
  contentText: string;
}): EngineArtifact {
  return {
    artifactType,
    path: fileName,
    contentText,
    contentJson: null,
    sha256: createHash("sha256").update(contentText).digest("hex"),
    sizeBytes: new TextEncoder().encode(contentText).byteLength,
    publicToUser: true,
    adminOnly: false,
    metadata: {
      file_name: fileName,
      ...syntheticMarker,
    },
  };
}

function buildSections(includeGenerated: boolean) {
  return [
    "Run configuration",
    "Candidate summary",
    "Evidence summary",
    ...(includeGenerated ? ["Generated hypotheses summary"] : []),
    "Validation summary",
    "Limitations",
  ];
}

function buildCandidateSummary() {
  return {
    ...syntheticMarker,
    count: 1,
    candidates: [
      {
        name: exampleCandidate,
        target: exampleTarget,
        disease_context: exampleDisease,
        summary: "Synthetic placeholder candidate for UI-test ranking only.",
        evidence_status: "synthetic_placeholder",
        identifiers: [],
      },
    ],
  };
}

function buildEvidenceSummary() {
  return {
    ...syntheticMarker,
    count: 1,
    evidence_items: [
      {
        label: "ExampleEvidenceA",
        candidate: exampleCandidate,
        target: exampleTarget,
        disease_context: exampleDisease,
        summary: "Synthetic placeholder evidence summary for UI tests only.",
        source_identifiers: [],
        direct_evidence: false,
      },
    ],
  };
}

function buildGeneratedHypothesesSummary(count: number) {
  return {
    ...syntheticMarker,
    count,
    hypotheses: Array.from({ length: count }, (_, index) => ({
      name: `ExampleGeneratedHypothesis${String.fromCharCode(65 + index)}`,
      candidate: exampleCandidate,
      target: exampleTarget,
      disease_context: exampleDisease,
      summary: "Synthetic computational placeholder hypothesis for UI tests only.",
      direct_evidence: false,
      requires_human_review: true,
    })),
  };
}

function buildValidationSummary() {
  return {
    ...syntheticMarker,
    status: "passed",
    warningCount: 1,
    checks: [
      "Synthetic markers present.",
      "No fake real source identifiers included.",
      "No disease-specific biomedical claims included.",
    ],
  };
}

export function createMockEngineArtifacts({
  run,
  options,
}: {
  project?: Project;
  run: ProductRun;
  options: ProductRunSafeOptions;
}): EngineArtifact[] {
  const generatedCount = generatedHypothesisCount(options);
  const sections = buildSections(generatedCount > 0);
  const candidateSummary = buildCandidateSummary();
  const evidenceSummary = buildEvidenceSummary();
  const validationSummary = buildValidationSummary();
  const resultBundleJson = {
    ...syntheticMarker,
    product_safe: true,
    status: "Ready for review",
    mode: options.mode,
    run: {
      id: run.id,
      mode: run.mode,
      disease_or_goal: exampleDisease,
      target_focus: exampleTarget,
    },
    sections,
    guardrails,
    limitations,
    candidateCount: candidateSummary.count,
    evidenceItemCount: evidenceSummary.count,
    generatedHypothesisCount: generatedCount,
    warningCount: validationSummary.warningCount,
    examples: {
      disease: exampleDisease,
      target: exampleTarget,
      candidate: exampleCandidate,
    },
  };
  const resultBundleMarkdown = [
    "# Synthetic Result Bundle",
    "",
    `Disease: ${exampleDisease}`,
    `Target: ${exampleTarget}`,
    `Candidate: ${exampleCandidate}`,
    "",
    "This deterministic V0.3 mock artifact is for UI tests only.",
    "",
    "No real source identifiers, biomedical claims, clinical claims, wet-lab steps, synthesis instructions, or dose-related guidance are included.",
  ].join("\n");
  const artifacts = [
    artifactFromJson({
      artifactType: "result_bundle_json",
      fileName: "v3_result_bundle.json",
      contentJson: resultBundleJson,
    }),
    artifactFromMarkdown({
      artifactType: "result_bundle_markdown",
      fileName: "v3_result_bundle.md",
      contentText: resultBundleMarkdown,
    }),
    artifactFromJson({
      artifactType: "candidates_json",
      fileName: "candidates_summary.json",
      contentJson: candidateSummary,
    }),
    artifactFromJson({
      artifactType: "evidence_json",
      fileName: "evidence_summary.json",
      contentJson: evidenceSummary,
    }),
    artifactFromJson({
      artifactType: "validation_json",
      fileName: "validation_summary.json",
      contentJson: validationSummary,
    }),
  ];

  if (generatedCount > 0) {
    artifacts.push(
      artifactFromJson({
        artifactType: "generated_candidates_json",
        fileName: "generated_hypotheses_summary.json",
        contentJson: buildGeneratedHypothesesSummary(generatedCount),
      }),
    );
  }

  return artifacts;
}

export function summarizeMockEngineArtifacts(artifacts: EngineArtifact[]): ProductArtifactSummary {
  const resultBundle = artifacts.find((artifact) => artifact.artifactType === "result_bundle_json")?.contentJson ?? {};

  return {
    sections: Array.isArray(resultBundle.sections) ? resultBundle.sections.filter((item): item is string => typeof item === "string") : [],
    candidateCount: typeof resultBundle.candidateCount === "number" ? resultBundle.candidateCount : 0,
    evidenceItemCount: typeof resultBundle.evidenceItemCount === "number" ? resultBundle.evidenceItemCount : 0,
    generatedHypothesisCount: typeof resultBundle.generatedHypothesisCount === "number" ? resultBundle.generatedHypothesisCount : 0,
    warningCount: typeof resultBundle.warningCount === "number" ? resultBundle.warningCount : 0,
    validationStatus: "passed",
  };
}

export async function runMockProductSafeDiscoveryWorkflow({
  project,
  run,
  options,
}: {
  project: Project;
  run: ProductRun;
  options: ProductRunSafeOptions;
}): Promise<ProductSafeResultBundle> {
  const artifacts = createMockEngineArtifacts({ project, run, options });
  const summary = summarizeMockEngineArtifacts(artifacts);

  return {
    artifactType: "result_bundle_json",
    displayName: `${project.name} synthetic result bundle`,
    summary: {
      status: "Ready for review",
      sections: summary.sections,
      candidateCount: summary.candidateCount,
      evidenceItemCount: summary.evidenceItemCount,
      generatedHypothesisCount: summary.generatedHypothesisCount,
      warningCount: summary.warningCount,
      mode: options.mode,
    },
    payload: {
      product_safe: true,
      ...syntheticMarker,
      project: {
        id: project.id,
        name: project.name,
        disease_focus: exampleDisease,
        target_focus: exampleTarget,
      },
      run: {
        id: run.id,
        disease_or_goal: exampleDisease,
        mode: run.mode,
      },
      sections: summary.sections,
      guardrails,
      limitations,
      counts: {
        ranked_candidates: summary.candidateCount,
        evidence_items: summary.evidenceItemCount,
        generated_hypotheses: summary.generatedHypothesisCount,
        warnings: summary.warningCount,
      },
    },
  };
}
