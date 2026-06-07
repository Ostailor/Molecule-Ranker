export const uiDemoMetadata = {
  synthetic: true,
  for_ui_demo_only: true,
} as const;

export type MockMetadata = typeof uiDemoMetadata;

export type ProjectStatus = "Active" | "Review" | "Paused";
export type RunStatus = "Complete" | "Running" | "Needs review";
export type CandidateStatus = "Review only" | "Needs research review" | "Saved for discussion";
export type ConfidenceBand = "Low" | "Medium" | "High";

export type PilotUser = {
  id: string;
  name: string;
  email: string;
  role: string;
  metadata: MockMetadata;
};

export type Organization = {
  id: string;
  name: string;
  plan: string;
  workspaceType: string;
  metadata: MockMetadata;
};

export type Project = {
  id: string;
  name: string;
  objective: string;
  status: ProjectStatus;
  therapeuticArea: string;
  updatedAt: string;
  runCount: number;
  metadata: MockMetadata;
};

export type DiscoveryRun = {
  id: string;
  projectId: string;
  name: string;
  mode: "mocked" | "dry_run";
  status: RunStatus;
  startedAt: string;
  candidateCount: number;
  generatedCount: number;
  evidenceCount: number;
  metadata: MockMetadata;
};

export type ResultBundle = {
  id: string;
  runId: string;
  name: string;
  status: "Draft" | "Ready for review" | "Archived";
  exportedAt: string | null;
  sections: string[];
  metadata: MockMetadata;
};

export type RankedCandidate = {
  id: string;
  name: string;
  rank: number;
  score: number;
  confidence: ConfidenceBand;
  modality: string;
  status: CandidateStatus;
  evidenceCount: number;
  warnings: string[];
  tags: string[];
  targetName: string;
  metadata: MockMetadata;
};

export type EvidenceItem = {
  id: string;
  candidateId: string;
  sourceType: string;
  title: string;
  summary: string;
  confidence: ConfidenceBand;
  synthetic: true;
  metadata: MockMetadata;
};

export type GeneratedHypothesis = {
  id: string;
  parentCandidateName: string;
  hypothesisType: string;
  score: number;
  warnings: string[];
  noDirectEvidence: true;
  metadata: MockMetadata;
};

export type UsageSummary = {
  discoveryRunsThisMonth: number;
  evidenceItemsReviewed: number;
  generatedHypotheses: number;
  reviewCheckpoints: number;
  monthlyRunLimit: number;
  metadata: MockMetadata;
};

export type FeedbackMessage = {
  id: string;
  category: string;
  message: string;
  status: "Open" | "Reviewed";
  metadata: MockMetadata;
};

export type AdminSummary = {
  workspaceCount: number;
  pilotSeats: number;
  pendingReviews: number;
  adminNotice: string;
  metadata: MockMetadata;
};

export const pilotUser: PilotUser = {
  id: "pilot-user-example-a",
  name: "Example Pilot User",
  email: "pilot@example.test",
  role: "Research reviewer",
  metadata: uiDemoMetadata,
};

export const organization: Organization = {
  id: "org-example-a",
  name: "Example Research Organization",
  plan: "Pilot preview",
  workspaceType: "Synthetic UI demo workspace",
  metadata: uiDemoMetadata,
};

export const projects: Project[] = [
  {
    id: "project-example-a",
    name: "ExampleDiseaseA hypothesis review",
    objective:
      "Review synthetic candidate ranking outputs for ExampleDiseaseA planning workflows without implying biomedical evidence.",
    status: "Active",
    therapeuticArea: "ExampleDiseaseA",
    updatedAt: "2026-06-06T14:35:00-04:00",
    runCount: 3,
    metadata: uiDemoMetadata,
  },
  {
    id: "project-example-b",
    name: "ExampleDiseaseB result bundle rehearsal",
    objective:
      "Evaluate result bundle structure, reviewer notes, and limitations using only UI demo placeholders.",
    status: "Review",
    therapeuticArea: "ExampleDiseaseB",
    updatedAt: "2026-06-05T09:10:00-04:00",
    runCount: 2,
    metadata: uiDemoMetadata,
  },
  {
    id: "project-example-c",
    name: "ExampleDiseaseC candidate ranking sandbox",
    objective: "Compare synthetic ranking states and warning surfaces for researcher-facing workflow review.",
    status: "Paused",
    therapeuticArea: "ExampleDiseaseC",
    updatedAt: "2026-06-03T16:22:00-04:00",
    runCount: 1,
    metadata: uiDemoMetadata,
  },
];

export const runs: DiscoveryRun[] = [
  {
    id: "run-example-a",
    projectId: "project-example-a",
    name: "ExampleDiseaseA discovery run",
    mode: "dry_run",
    status: "Complete",
    startedAt: "2026-06-06T14:35:00-04:00",
    candidateCount: 4,
    generatedCount: 3,
    evidenceCount: 4,
    metadata: uiDemoMetadata,
  },
  {
    id: "run-example-b",
    projectId: "project-example-a",
    name: "Synthetic candidate ranking refresh",
    mode: "mocked",
    status: "Needs review",
    startedAt: "2026-06-05T17:12:00-04:00",
    candidateCount: 3,
    generatedCount: 2,
    evidenceCount: 3,
    metadata: uiDemoMetadata,
  },
  {
    id: "run-example-c",
    projectId: "project-example-a",
    name: "Result bundle layout rehearsal",
    mode: "mocked",
    status: "Running",
    startedAt: "2026-06-05T11:45:00-04:00",
    candidateCount: 2,
    generatedCount: 1,
    evidenceCount: 2,
    metadata: uiDemoMetadata,
  },
];

export const resultBundles: ResultBundle[] = [
  {
    id: "bundle-example-a",
    runId: "run-example-a",
    name: "ExampleDiseaseA UI demo result bundle",
    status: "Ready for review",
    exportedAt: "2026-06-06T15:20:00-04:00",
    sections: ["Candidate ranking", "Evidence", "Generated hypotheses", "Limitations", "Research notes"],
    metadata: uiDemoMetadata,
  },
  {
    id: "bundle-example-b",
    runId: "run-example-b",
    name: "Synthetic review packet",
    status: "Draft",
    exportedAt: null,
    sections: ["Candidate ranking", "Evidence", "Limitations"],
    metadata: uiDemoMetadata,
  },
];

export const candidates: RankedCandidate[] = [
  {
    id: "candidate-example-a",
    name: "ExampleCandidateA",
    rank: 1,
    score: 0.82,
    confidence: "Medium",
    modality: "Small molecule placeholder",
    status: "Review only",
    evidenceCount: 2,
    warnings: ["Synthetic UI demo item", "No direct biomedical evidence"],
    tags: ["ExampleTargetA", "Candidate ranking", "Research notes"],
    targetName: "ExampleTargetA",
    metadata: uiDemoMetadata,
  },
  {
    id: "candidate-example-b",
    name: "ExampleCandidateB",
    rank: 2,
    score: 0.74,
    confidence: "Low",
    modality: "Peptide placeholder",
    status: "Needs research review",
    evidenceCount: 1,
    warnings: ["Synthetic UI demo item", "Evidence summary is not source-backed"],
    tags: ["ExampleTargetB", "Result bundle"],
    targetName: "ExampleTargetB",
    metadata: uiDemoMetadata,
  },
  {
    id: "candidate-example-c",
    name: "ExampleCandidateC",
    rank: 3,
    score: 0.69,
    confidence: "Low",
    modality: "Antibody placeholder",
    status: "Saved for discussion",
    evidenceCount: 1,
    warnings: ["Synthetic UI demo item", "Prioritization score is for layout testing only"],
    tags: ["ExampleTargetC", "Limitations"],
    targetName: "ExampleTargetC",
    metadata: uiDemoMetadata,
  },
  {
    id: "candidate-example-d",
    name: "ExampleCandidateD",
    rank: 4,
    score: 0.61,
    confidence: "Low",
    modality: "Generated hypothesis placeholder",
    status: "Needs research review",
    evidenceCount: 0,
    warnings: ["Synthetic UI demo item", "No evidence items linked"],
    tags: ["ExampleTargetD", "Generated hypotheses"],
    targetName: "ExampleTargetD",
    metadata: uiDemoMetadata,
  },
];

export const evidenceItems: EvidenceItem[] = [
  {
    id: "evidence-example-a",
    candidateId: "candidate-example-a",
    sourceType: "Example Evidence Source",
    title: "Synthetic evidence summary A",
    summary:
      "Demo-only summary for ExampleCandidateA. This is not source-backed biomedical evidence and should not be cited.",
    confidence: "Medium",
    synthetic: true,
    metadata: uiDemoMetadata,
  },
  {
    id: "evidence-example-b",
    candidateId: "candidate-example-a",
    sourceType: "Example Evidence Source",
    title: "Synthetic provenance note A",
    summary: "Placeholder provenance row used to test evidence table spacing, confidence labels, and review warnings.",
    confidence: "Low",
    synthetic: true,
    metadata: uiDemoMetadata,
  },
  {
    id: "evidence-example-c",
    candidateId: "candidate-example-b",
    sourceType: "Example Evidence Source",
    title: "Synthetic evidence summary B",
    summary: "Demo-only row for ExampleCandidateB. It intentionally avoids external identifiers and disease claims.",
    confidence: "Low",
    synthetic: true,
    metadata: uiDemoMetadata,
  },
  {
    id: "evidence-example-d",
    candidateId: "candidate-example-c",
    sourceType: "Example Evidence Source",
    title: "Synthetic review note C",
    summary: "Reviewer-facing placeholder showing how limitations can be listed in a result bundle.",
    confidence: "Low",
    synthetic: true,
    metadata: uiDemoMetadata,
  },
];

export const generatedHypotheses: GeneratedHypothesis[] = [
  {
    id: "hypothesis-example-a",
    parentCandidateName: "ExampleCandidateA",
    hypothesisType: "Synthetic generated hypothesis",
    score: 0.71,
    warnings: ["No direct evidence", "For UI demo only"],
    noDirectEvidence: true,
    metadata: uiDemoMetadata,
  },
  {
    id: "hypothesis-example-b",
    parentCandidateName: "ExampleCandidateB",
    hypothesisType: "Synthetic generated hypothesis",
    score: 0.64,
    warnings: ["No direct evidence", "Requires research review before use"],
    noDirectEvidence: true,
    metadata: uiDemoMetadata,
  },
  {
    id: "hypothesis-example-c",
    parentCandidateName: "ExampleCandidateC",
    hypothesisType: "Synthetic generated hypothesis",
    score: 0.58,
    warnings: ["No direct evidence", "Placeholder prioritization score"],
    noDirectEvidence: true,
    metadata: uiDemoMetadata,
  },
];

export const usageSummary: UsageSummary = {
  discoveryRunsThisMonth: 6,
  evidenceItemsReviewed: 14,
  generatedHypotheses: 3,
  reviewCheckpoints: 5,
  monthlyRunLimit: 12,
  metadata: uiDemoMetadata,
};

export const feedbackMessages: FeedbackMessage[] = [
  {
    id: "feedback-example-a",
    category: "Result bundle",
    message: "Synthetic feedback placeholder about making limitations easier to scan.",
    status: "Open",
    metadata: uiDemoMetadata,
  },
  {
    id: "feedback-example-b",
    category: "Candidate ranking",
    message: "Synthetic feedback placeholder about sorting candidates by warning count.",
    status: "Reviewed",
    metadata: uiDemoMetadata,
  },
];

export const adminSummary: AdminSummary = {
  workspaceCount: 1,
  pilotSeats: 4,
  pendingReviews: 2,
  adminNotice: "Synthetic admin summary for Release V0.1 UI review.",
  metadata: uiDemoMetadata,
};
