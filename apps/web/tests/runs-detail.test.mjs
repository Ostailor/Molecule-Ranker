import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("discovery run detail page", () => {
  it("renders queued and running states with polling and cancel controls", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/page.tsx");
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(page, /requireUser\(`\/login\?next=\/projects\/\$\{projectId\}\/runs\/\$\{runId\}`\)/);
    assert.match(page, /\.from\("product_runs"\)/);
    assert.match(page, /\.eq\("organization_id", membership\.organization_id\)/);
    assert.doesNotMatch(page, /searchParams|query\?\.state/);
    assert.match(summary, /"use client"/);
    assert.match(summary, /fetch\(`\/api\/product\/projects\/\$\{liveRun\.project_id\}\/runs\/\$\{liveRun\.id\}\/status`/);
    assert.match(summary, /window\.setInterval/);
    assert.match(summary, /4000/);
    assert.match(summary, /isTerminalState\(liveState\)/);
    assert.match(summary, /liveState === "queued" \|\| liveState === "running"/);
    assert.match(summary, /Cancel run/);
    assert.match(summary, /\/cancel`/);

    for (const text of ["Queued", "Running", "Result bundle pending", "Run mode:", "Disease or goal"]) {
      assert.match(summary + page, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });

  it("succeeded and partial states link to the result bundle", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/page.tsx");
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(page, /runState === "completed" \|\| runState === "partial"/);
    assert.match(page, /const resultHref = `\/projects\/\$\{projectId\}\/runs\/\$\{runId\}\/result`/);
    assert.match(page, /View result bundle/);
    assert.match(summary, /Result bundle ready/);
    assert.match(summary, /href=\{resultHref\}/);
    assert.match(summary, /Partial result warning/);
    assert.match(summary, /View result bundle/);
  });

  it("failed state shows safe error summary and researcher-facing remediation", () => {
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(summary, /runState === "failed"/);
    assert.match(summary, /Run needs attention/);
    assert.match(summary, /errorSummary \|\| "The bounded workflow could not prepare a product-safe result bundle\."/);
    assert.match(summary, /Review the project objective/);
    assert.match(summary, /keep the request bounded\s+to research planning/);
    assert.doesNotMatch(summary, /stack trace|exception|stderr|debug output/i);
  });

  it("handles forbidden and missing run states safely", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/page.tsx");
    const summary = read("src/components/runs/run-summary.tsx");

    assert.match(page, /Discovery run not found/);
    assert.match(page, /No accessible run matches this project in your active organization/);
    assert.match(page, /Tenant-scoped lookup/);
    assert.match(summary, /response\.status === 401 \|\| response\.status === 403 \|\| response\.status === 404/);
    assert.match(summary, /This run is not available in the current organization/);
    assert.match(summary, /Could not refresh run status/);
    assert.doesNotMatch(page + summary, /error\.stack|JSON\.stringify\(error\)|console\.error/);
  });

  it("renders the requested progress timeline and safe states", () => {
    const summary = read("src/components/runs/run-summary.tsx");

    for (const text of [
      "Queued",
      "Preparing product-safe run context",
      "Executing bounded discovery workflow",
      "Filtering product-safe artifacts",
      "Creating generated hypotheses if enabled",
      "Building result bundle",
      "Completed",
      "progressRecord",
      "progress.step",
      "progress.message",
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
      "raw stdout",
      "raw stderr",
      "raw traces",
      "raw logs",
      "raw engine trace",
      "raw Codex transcript",
      "internal stack traces",
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
