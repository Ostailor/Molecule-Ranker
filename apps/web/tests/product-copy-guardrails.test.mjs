import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const srcDir = join(root, "src");

const forbiddenPhrases = [
  "cure",
  "cures",
  "treatment recommendation",
  "recommended treatment",
  "safe molecule",
  "proven safe",
  "proven effective",
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
  "not a cure finder",
  "not a lab protocol",
  "not a lab protocol generator",
  "no lab protocols",
  "no dosing",
  "do not request treatment, dosing, synthesis, or lab protocols",
  "not evidence of safety, efficacy, binding, or therapeutic value",
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

describe("product copy guardrails", () => {
  it("does not include unsafe marketing or product claims in web source copy", () => {
    const findings = [];

    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf8");

      for (const phrase of forbiddenPhrases) {
        const pattern = phrasePattern(phrase);
        const matches = text.matchAll(new RegExp(pattern.source, `${pattern.flags.includes("i") ? "i" : ""}g`));

        for (const match of matches) {
          const index = match.index ?? 0;
          const excerpt = excerptFor(text, index);
          if (isAllowedContext(contextFor(text, index))) continue;

          findings.push({
            file: relative(root, file),
            line: lineNumberFor(text, index),
            phrase,
            excerpt,
          });
        }
      }
    }

    assert.deepEqual(findings, []);
  });
});
