import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product usage events", () => {
  it("defines V0.2 usage actions and V0.3 workflow actions in one web helper", () => {
    const usage = read("src/lib/product/usage.ts");

    assert.ok(existsSync(join(root, "src/lib/product/usage.ts")));

    for (const action of [
      "create_project",
      "feedback_create",
      "login",
      "onboarding_complete",
      "run_discovery",
      "generated_hypotheses",
      "export_result",
      "codex_task",
    ]) {
      assert.match(usage, new RegExp(`"${action}"`));
    }

    assert.match(usage, /export const v03UsageActions/);
    assert.match(usage, /export async function recordUsageEvent/);
    assert.match(usage, /export async function getUsageSummaryForOrg/);
    assert.match(usage, /export async function checkUsageAllowed/);
    assert.match(usage, /product_usage_events/);
    assert.doesNotMatch(usage, /SUPABASE_SERVICE_ROLE_KEY|service_role|serviceRole/);
  });

  it("records project, run, and feedback usage from authenticated org context", () => {
    const projectAction = read("src/lib/supabase/project-actions.ts");
    const projectApi = read("src/app/api/product/projects/route.ts");
    const runsApi = read("src/app/api/product/projects/[projectId]/runs/route.ts");
    const feedbackApi = read("src/app/api/product/feedback/route.ts");

    assert.match(projectAction, /checkUsageAllowed\("create_project", 1, \{ supabaseClient: supabase \}\)/);
    assert.match(projectAction, /recordUsageEvent\("create_project", 1, \{ project_id: data\.id \}/);
    assert.match(projectApi, /checkUsageAllowed\("create_project", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(projectApi, /recordUsageEvent\("create_project", 1, \{ project_id: data\.id \}/);
    assert.match(feedbackApi, /checkUsageAllowed\("feedback_create", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(feedbackApi, /recordUsageEvent\("feedback_create", 1, \{ category, feedback_id: data\.id \}/);
    assert.match(runsApi, /checkRunUsageLimits\(\{ context, supabase, options \}\)/);
    assert.match(runsApi, /checkUsageAllowed\("run_discovery", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(runsApi, /checkUsageAllowed\("generated_hypotheses", generatedHypothesisCount, \{ context, supabaseClient: supabase \}\)/);
    assert.match(runsApi, /checkUsageAllowed\("codex_task", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(runsApi, /recordRunUsageEvents\(\{ context, supabase, projectId, run, options \}\)/);
    assert.match(runsApi, /recordUsageEvent\(\s*"run_discovery"/);
    assert.match(runsApi, /recordUsageEvent\(\s*"generated_hypotheses"/);
    assert.match(runsApi, /recordUsageEvent\(\s*"codex_task"/);
  });

  it("summarizes usage events by action for the current organization", () => {
    const usage = read("src/lib/product/usage.ts");

    assert.match(usage, /context\.organization\.id !== orgId/);
    assert.match(usage, /throw productApiError\("FORBIDDEN"\)/);
    assert.match(usage, /\.from\("product_usage_events"\)/);
    assert.match(usage, /\.eq\("organization_id", orgId\)/);
    assert.match(usage, /\.gte\("created_at", periodStart\)/);
    assert.match(usage, /summary\.quantity \+= quantity/);
    assert.match(usage, /summary\.eventCount \+= 1/);
    assert.match(usage, /totalQuantityThisMonth/);
  });

  it("blocks configured low plan limits before writes", () => {
    const usage = read("src/lib/product/usage.ts");

    assert.match(usage, /limits\?: ProductUsagePlanConfig/);
    assert.match(usage, /getPlanUsageLimits\(context\.plan, options\.limits\)/);
    assert.match(usage, /projectedQuantity > limit/);
    assert.match(usage, /throw productApiError\("PLAN_LIMIT_EXCEEDED"/);
    assert.match(usage, /Release V0\.3/);
  });
});
