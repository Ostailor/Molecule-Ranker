import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const srcDir = join(root, "src");

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

function sourceFiles(dir = srcDir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(path) ? [path] : [];
  });
}

function sourceText() {
  return sourceFiles()
    .map((file) => readFileSync(file, "utf8"))
    .join("\n");
}

describe("product shell layout", () => {
  it("renders the expected shell regions", () => {
    const shell = read("src/components/layout/app-shell.tsx");

    assert.match(shell, /<SideNav\s*\/>/);
    assert.match(shell, /<TopNav\s*\/>/);
    assert.match(shell, /<ResearchUseBanner\s*\/>/);
    assert.match(shell, /<main\b/);
    assert.match(shell, /<Footer\s*\/>/);
  });

  it("defines the required navigation links", () => {
    const routes = read("src/lib/routes.ts");
    const expectedLabels = [
      "Dashboard",
      "Projects",
      "New Discovery Run",
      "Result Bundles",
      "Saved Candidates",
      "Usage",
      "Feedback",
      "Account",
      "Admin",
    ];

    for (const label of expectedLabels) {
      assert.match(routes, new RegExp(`label: "${label}"`));
    }

    assert.match(routes, /href: "\/dashboard"/);
    assert.match(routes, /href: "\/usage"/);
    assert.match(routes, /href: "\/feedback"/);
    assert.match(routes, /href: "\/account"/);
    assert.match(routes, /href: "\/admin"/);
    assert.match(routes, /adminOnly: true/);
  });

  it("includes the persistent research-use disclaimer", () => {
    const text = sourceText();
    assert.match(
      text,
      /This platform generates research-planning artifacts and hypotheses\. It is not medical advice, clinical validation, a lab protocol generator, or a synthesis planner\./,
    );
  });

  it("does not expose forbidden internal terms or product claims", () => {
    const text = sourceText();
    const forbidden = [
      "AgentGraph",
      "MCP",
      "governance policy",
      "repair loops",
      "raw Codex transcripts",
      "kill switches",
      "tool marketplace",
      "cures",
      "validated drug",
      "safe",
      "effective",
      "active binder",
      "clinical proof",
    ];

    for (const phrase of forbidden) {
      assert.doesNotMatch(text, new RegExp(`\\b${phrase.replaceAll(" ", "\\s+")}\\b`, "i"));
    }
  });
});
