import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("discovery run detail page", () => {
  it("completed run links to the result bundle", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/page.tsx");
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(page, /runState === "completed"/);
    assert.match(page, /const resultHref = `\/projects\/\$\{projectId\}\/runs\/\$\{runId\}\/result`/);
    assert.match(page, /View result bundle/);
    assert.match(summary, /Result bundle ready/);
    assert.match(summary, /href=\{resultHref\}/);
  });

  it("failed state gives researcher-facing remediation", () => {
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(summary, /runState === "failed"/);
    assert.match(summary, /Run needs attention/);
    assert.match(summary, /The mock workflow could not prepare a result bundle/);
    assert.match(summary, /Review the project objective/);
    assert.match(summary, /keep the request bounded\s+to research planning/);
    assert.doesNotMatch(summary, /stack trace|exception|stderr|debug|internal/i);
  });

  it("renders the requested coarse timeline and states", () => {
    const summary = read("src/components/runs/run-summary.tsx");

    for (const text of [
      "Queued",
      "Resolving disease/project context",
      "Retrieving source-backed candidates",
      "Reviewing evidence",
      "Creating generated hypotheses if enabled",
      "Building result bundle",
      "Completed",
      "queued",
      "running",
      "completed",
      "failed",
      "partial",
      "cancelled",
      "Partial result warning",
    ]) {
      assert.match(summary, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });

  it("does not expose internal execution terms", () => {
    const source = `${read("src/app/projects/[projectId]/runs/[runId]/page.tsx")}\n${read(
      "src/components/runs/run-summary.tsx",
    )}`;
    const forbidden = [
      "AgentGraph",
      "raw Codex planning",
      "raw traces",
      "raw logs",
      "governance internals",
      "MCP",
      "repair loops",
      "kill switches",
      "tool marketplace",
    ];

    for (const phrase of forbidden) {
      assert.doesNotMatch(source, new RegExp(`\\b${phrase.replaceAll(" ", "\\s+")}\\b`, "i"));
    }
  });
});
