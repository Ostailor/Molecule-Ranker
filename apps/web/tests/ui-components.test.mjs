import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("reusable UI components", () => {
  it("core components render through exported functions", () => {
    const files = [
      ["Button", "src/components/ui/button.tsx"],
      ["Card", "src/components/ui/card.tsx"],
      ["Badge", "src/components/ui/badge.tsx"],
      ["Alert", "src/components/ui/alert.tsx"],
      ["Table", "src/components/ui/table.tsx"],
      ["Tabs", "src/components/ui/tabs.tsx"],
      ["EmptyState", "src/components/ui/empty-state.tsx"],
      ["LoadingSkeleton", "src/components/ui/loading-skeleton.tsx"],
      ["ErrorState", "src/components/ui/error-state.tsx"],
      ["StatCard", "src/components/ui/stat-card.tsx"],
      ["StepTimeline", "src/components/ui/step-timeline.tsx"],
      ["DisclaimerBanner", "src/components/ui/disclaimer-banner.tsx"],
      ["FeatureFlagGate", "src/components/ui/feature-flag-gate.tsx"],
      ["UsageMeter", "src/components/ui/usage-meter.tsx"],
      ["ScoreBadge", "src/components/ui/score-badge.tsx"],
      ["ConfidenceBadge", "src/components/ui/confidence-badge.tsx"],
      ["WarningList", "src/components/ui/warning-list.tsx"],
      ["SyntheticDataNotice", "src/components/ui/synthetic-data-notice.tsx"],
    ];

    for (const [exportName, relativePath] of files) {
      const absolutePath = join(root, relativePath);
      assert.ok(existsSync(absolutePath), `${relativePath} should exist`);
      assert.match(read(relativePath), new RegExp(`export function ${exportName}\\b`));
    }
  });

  it("FeatureFlagGate hides disabled features", () => {
    const source = read("src/components/ui/feature-flag-gate.tsx");

    assert.match(source, /enabled: boolean/);
    assert.match(source, /if \(!enabled\) return fallback/);
    assert.match(source, /return <>\{children\}<\/>/);
  });

  it("DisclaimerBanner renders the canonical research-use text", () => {
    const source = read("src/components/ui/disclaimer-banner.tsx");
    const disclaimers = read("src/lib/disclaimers.ts");

    assert.match(source, /researchUseDisclaimer/);
    assert.match(
      disclaimers,
      /This platform generates research-planning artifacts and hypotheses\. It is not medical advice, clinical validation, a lab protocol generator, or a synthesis planner\./,
    );
  });

  it("accessibility hooks are present where practical", () => {
    assert.match(read("src/components/ui/tabs.tsx"), /role="tablist"/);
    assert.match(read("src/components/ui/tabs.tsx"), /aria-selected/);
    assert.match(read("src/components/ui/loading-skeleton.tsx"), /role="status"/);
    assert.match(read("src/components/ui/error-state.tsx"), /role="alert"/);
    assert.match(read("src/components/ui/table.tsx"), /<caption className="sr-only">/);
  });
});
