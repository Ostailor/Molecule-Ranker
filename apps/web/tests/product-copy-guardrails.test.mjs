import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const srcDir = join(root, "src");

const forbiddenPhrases = [
  "cure",
  "cures",
  "treats disease",
  "treatment recommendation",
  "recommended treatment",
  "safe molecule",
  "proven safe",
  "proven effective",
  "active binder",
  "clinical validation",
  "validated drug",
  "guaranteed",
  "binds target",
  "active molecule",
  "synthesis protocol",
  "lab protocol",
  "dosing",
  "patient treatment",
  "administer",
  "animal dosing",
  "human dosing",
];

const allowedContexts = [
  "not medical advice",
  "not clinical validation",
  "does not provide dosing",
  "does not provide lab protocols",
  "does not claim binding/activity/safety",
  "binding/activity/safety is not claimed",
  "binds target is not claimed",
  "not a cure finder",
  "not a lab protocol",
  "not a lab protocol generator",
  "not a synthesis plan",
  "no lab protocols",
  "no dosing",
  "do not request treatment, dosing, synthesis, or lab protocols",
  "not evidence of safety, efficacy, binding, or therapeutic value",
];

const requiredResultBundleDisclaimers = [
  "research-planning artifact",
  "not medical advice",
  "not clinical validation",
  "not a lab protocol",
  "not a synthesis plan",
  "generated hypotheses are computational only",
];

function sourceFiles(dir = srcDir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(path) ? [path] : [];
  });
}

function phrasePattern(phrase) {
  const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replaceAll("\\ ", "\\s+");
  if (phrase === "lab protocol") return /\blab\s+protocol(?:s|\s+generator)?\b/i;
  if (phrase === "dosing") return /\bdosing(?:\s+guidance)?\b/i;
  return new RegExp(`\\b${escaped}\\b`, "i");
}

function lineNumberFor(text, index) {
  return text.slice(0, index).split("\n").length;
}

function excerptFor(text, index) {
  const lineStart = text.lastIndexOf("\n", index) + 1;
  const lineEnd = text.indexOf("\n", index);
  return text.slice(lineStart, lineEnd === -1 ? text.length : lineEnd).trim();
}

function contextFor(text, index) {
  return text.slice(Math.max(0, index - 180), Math.min(text.length, index + 180));
}

function isAllowedContext(context) {
  const normalized = context.toLowerCase().replace(/\s+/g, " ");
  return allowedContexts.some((context) => normalized.includes(context));
}

function scanUnsafeCopy(text, file = "inline") {
  const findings = [];

  for (const phrase of forbiddenPhrases) {
    const pattern = phrasePattern(phrase);
    const matches = text.matchAll(new RegExp(pattern.source, `${pattern.flags.includes("i") ? "i" : ""}g`));

    for (const match of matches) {
      const index = match.index ?? 0;
      const excerpt = excerptFor(text, index);
      if (isAllowedContext(contextFor(text, index))) continue;

      findings.push({
        file,
        line: lineNumberFor(text, index),
        phrase,
        excerpt,
      });
    }
  }

  return findings;
}

describe("product copy guardrails", () => {
  it("does not include unsafe marketing or product claims in web source copy", () => {
    const findings = sourceFiles().flatMap((file) => scanUnsafeCopy(readFileSync(file, "utf8"), relative(root, file)));

    assert.deepEqual(findings, []);
  });

  it("scans V0.3 run/result templates and product artifact summaries", () => {
    const sources = [
      "src/lib/product/engine-runner.ts",
      "src/lib/product/mock-engine-runner.ts",
      "src/lib/product/artifact-filter.ts",
      "src/components/runs/result-bundle-overview.tsx",
    ];
    const combined = sources.map((file) => readFileSync(join(root, file), "utf8")).join("\n");

    assert.deepEqual(scanUnsafeCopy(combined, "v0.3-result-output-sources"), []);

    const normalized = combined.toLowerCase().replace(/\s+/g, " ");
    for (const disclaimer of requiredResultBundleDisclaimers) {
      assert.match(normalized, new RegExp(disclaimer.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });

  it("forbidden examples fail the copy scanner", () => {
    const forbiddenExamples = [
      "This product cures ExampleDiseaseA.",
      "This output treats disease.",
      "This is a treatment recommendation.",
      "ExampleCandidateA is a safe molecule.",
      "ExampleCandidateA is proven safe.",
      "ExampleCandidateA is proven effective.",
      "ExampleCandidateA is an active binder.",
      "ExampleCandidateA binds target ExampleTargetA.",
      "This is clinical validation.",
      "ExampleCandidateA is a validated drug.",
      "Use this synthesis protocol.",
      "Follow this lab protocol.",
      "Provide dosing guidance.",
      "Use for patient treatment.",
      "Administer ExampleCandidateA.",
      "Animal dosing is included.",
      "Human dosing is included.",
    ];

    for (const example of forbiddenExamples) {
      assert.ok(scanUnsafeCopy(example).length > 0, `Expected forbidden example to fail: ${example}`);
    }
  });

  it("safe disclaimers pass the copy scanner", () => {
    const safeDisclaimer = `
      This result bundle is a research-planning artifact.
      It is not medical advice.
      It is not clinical validation.
      It is not a lab protocol.
      It is not a synthesis plan.
      Generated hypotheses are computational only.
      Binding/activity/safety is not claimed.
      This does not provide dosing.
      Do not request treatment, dosing, synthesis, or lab protocols.
    `;

    assert.deepEqual(scanUnsafeCopy(safeDisclaimer), []);
  });
});
