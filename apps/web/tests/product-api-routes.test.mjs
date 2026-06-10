import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product API route stubs", () => {
  it("defines the requested product API routes through V0.3", () => {
    for (const route of [
      "src/app/api/product/me/route.ts",
      "src/app/api/product/projects/route.ts",
      "src/app/api/product/projects/[projectId]/route.ts",
      "src/app/api/product/projects/[projectId]/runs/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/cancel/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/[artifactId]/route.ts",
      "src/app/api/product/projects/[projectId]/runs/[runId]/result/route.ts",
      "src/app/api/product/usage/route.ts",
      "src/app/api/product/feedback/route.ts",
      "src/app/api/product/admin/summary/route.ts",
    ]) {
      assert.ok(existsSync(join(root, route)), `${route} should exist`);
    }
  });

  it("requires auth for all current product API endpoints and returns sanitized failures", () => {
    const routeSources = [
      read("src/app/api/product/me/route.ts"),
      read("src/app/api/product/projects/route.ts"),
      read("src/app/api/product/projects/[projectId]/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/cancel/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/[artifactId]/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/result/route.ts"),
      read("src/app/api/product/usage/route.ts"),
      read("src/app/api/product/feedback/route.ts"),
      read("src/app/api/product/admin/summary/route.ts"),
    ].join("\n");
    const apiErrors = read("src/lib/product/api-errors.ts");

    assert.match(routeSources, /getProductAuthContext|requireProductPermission|requireOrganizationMember|requireAdminRole/);
    assert.match(routeSources, /sanitizeProductApiError\(error\)/);
    assert.match(routeSources, /failure\(apiError\.code, apiError\.publicMessage, apiError\.status\)/);
    assert.match(apiErrors, /UNAUTHENTICATED: 401/);
    assert.match(apiErrors, /FORBIDDEN: 403/);
    assert.doesNotMatch(routeSources, /error\.message|JSON\.stringify\(error\)|stack|service_role|SUPABASE_SERVICE_ROLE_KEY/);
  });

  it("me endpoint returns sanitized auth context", () => {
    const route = read("src/app/api/product/me/route.ts");

    for (const field of ["user", "profile", "organization", "membership", "role", "plan"]) {
      assert.match(route, new RegExp(`\\b${field}\\b`));
    }

    assert.match(route, /id: context\.user\.id/);
    assert.match(route, /email: context\.user\.email \?\? null/);
    assert.doesNotMatch(route, /identities|sessions|factors|app_metadata|user_metadata/);
  });

  it("projects endpoints are scoped to the active organization", () => {
    const listRoute = read("src/app/api/product/projects/route.ts");
    const detailRoute = read("src/app/api/product/projects/[projectId]/route.ts");

    assert.match(listRoute, /requireProductPermission\("project:read", supabase\)/);
    assert.match(listRoute, /requireProductPermission\("project:create", supabase\)/);
    assert.match(listRoute, /\.from\("product_projects"\)/);
    assert.match(listRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(listRoute, /organization_id: context\.organization\.id/);
    assert.match(listRoute, /created_by_user_id: context\.user\.id/);
    assert.match(listRoute, /checkUsageAllowed\("create_project", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(listRoute, /recordUsageEvent\("create_project", 1, \{ project_id: data\.id \}/);
    assert.match(detailRoute, /\.eq\("id", projectId\)/);
    assert.match(detailRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(detailRoute, /throw productApiError\("NOT_FOUND"\)/);
  });

  it("usage and feedback endpoints use organization membership boundaries", () => {
    const usageRoute = read("src/app/api/product/usage/route.ts");
    const feedbackRoute = read("src/app/api/product/feedback/route.ts");

    assert.match(usageRoute, /requireOrganizationMember\(supabase\)/);
    assert.match(usageRoute, /getUsageSummaryForOrg\(context\.organization\.id, \{ context, supabaseClient: supabase \}\)/);
    assert.match(feedbackRoute, /requireProductPermission\("feedback:create", supabase\)/);
    assert.match(feedbackRoute, /\.from\("product_feedback"\)/);
    assert.match(feedbackRoute, /organization_id: context\.organization\.id/);
    assert.match(feedbackRoute, /user_id: context\.user\.id/);
    assert.match(feedbackRoute, /checkUsageAllowed\("feedback_create", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(feedbackRoute, /recordUsageEvent\("feedback_create", 1/);
    assert.match(feedbackRoute, /VALIDATION_ERROR/);
  });

  it("admin summary requires owner or admin role and blocks researchers", () => {
    const adminRoute = read("src/app/api/product/admin/summary/route.ts");
    const authContext = read("src/lib/product/auth-context.ts");
    const permissions = read("src/lib/product/permissions.ts");

    assert.match(adminRoute, /requireAdminRole\(supabase\)/);
    assert.match(authContext, /canAccessAdmin\(context\.role\)/);
    assert.match(permissions, /researcher: \[/);
    assert.doesNotMatch(permissions.match(/researcher: \[[^\]]+\]/s)?.[0] ?? "", /admin:read/);
    assert.match(adminRoute, /\.from\("product_projects"\)/);
    assert.match(adminRoute, /\.from\("product_memberships"\)/);
    assert.match(adminRoute, /\.from\("product_feedback"\)/);
    assert.match(adminRoute, /\.from\("product_usage_events"\)/);
  });

  it("V0.3 run APIs are tenant-scoped and hide raw workflow internals", () => {
    const runApiRoutes = [
      read("src/app/api/product/projects/[projectId]/runs/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/cancel/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/[artifactId]/route.ts"),
      read("src/app/api/product/projects/[projectId]/runs/[runId]/result/route.ts"),
    ];
    const routeSources = runApiRoutes.join("\n");
    const runner = read("src/lib/product/engine-runner.ts");
    const storage = read("src/lib/product/artifact-storage.ts");
    const worker = read("src/lib/product/run-worker.ts");

    assert.match(routeSources, /requireProductPermission\("run:create", supabase\)/);
    assert.match(routeSources, /requireProductPermission\("run:read", supabase\)/);
    assert.match(routeSources, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(routeSources, /\.from\("product_runs"\)/);
    assert.match(routeSources, /listRunArtifacts/);
    assert.match(storage, /\.from\("product_run_artifacts"\)/);
    assert.match(routeSources, /checkRunUsageLimits\(\{ context, supabase, options \}\)/);
    assert.match(routeSources, /checkUsageAllowed\("run_discovery", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(routeSources, /checkUsageAllowed\("generated_hypotheses", generatedHypothesisCount, \{ context, supabaseClient: supabase \}\)/);
    assert.match(routeSources, /checkUsageAllowed\("codex_task", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(routeSources, /recordUsageEvent\(\s*"run_discovery"/);
    assert.match(routeSources, /recordUsageEvent\(\s*"generated_hypotheses"/);
    assert.match(runner, /runProductSafeDiscoveryWorkflow/);
    assert.match(storage, /storeProductSafeResultArtifact/);
    assert.match(storage, /storeRunArtifact/);
    assert.match(worker, /executeProductRunWorker/);
    assert.match(worker, /runProductSafeDiscoveryWorkflow/);
    assert.match(worker, /storeProductSafeResultArtifact/);
    for (const source of runApiRoutes) {
      assert.match(source, /\[89ab\]\[0-9a-f\]\{3\}-\[0-9a-f\]\{12\}/);
      assert.doesNotMatch(source, /\[89ab\]\[0-9a-f\]\{12\}/);
    }
    assert.doesNotMatch(routeSources, /AgentGraph|raw Codex|transcript|trace|stderr|stdout|service_role/i);
    assert.ok(!existsSync(join(root, "src/app/api/product/candidates/route.ts")));
    assert.ok(!existsSync(join(root, "src/app/api/product/evidence/route.ts")));
    assert.ok(!existsSync(join(root, "src/app/api/product/generated/route.ts")));
  });

  it("run creation validates auth, roles, usage, tenant scope, and unsafe options", () => {
    const route = read("src/app/api/product/projects/[projectId]/runs/route.ts");
    const worker = read("src/lib/product/run-worker.ts");
    const runSafety = read("src/lib/product/run-safety.ts");
    const permissions = read("src/lib/product/permissions.ts");

    assert.match(route, /requireProductPermission\("run:create", supabase\)/);
    assert.match(route, /roleHasPermission\(context\.role, "project:read"\)/);
    assert.match(route, /getProjectForContext\(supabase, projectId, context\.organization\.id\)/);
    assert.match(route, /\.eq\("organization_id", organizationId\)/);
    assert.match(route, /readSafeRunOptions\(input\)/);
    assert.match(route, /input\.disease_or_goal \?\? input\.disease_project_objective/);
    assert.match(route, /checkRunUsageLimits\(\{ context, supabase, options \}\)/);
    assert.match(route, /checkUsageAllowed\("run_discovery", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(route, /checkUsageAllowed\("generated_hypotheses", generatedHypothesisCount, \{ context, supabaseClient: supabase \}\)/);
    assert.match(route, /checkUsageAllowed\("codex_task", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(route, /recordRunUsageEvents\(\{ context, supabase, projectId, run, options \}\)/);
    assert.match(route, /recordUsageEvent\(\s*"run_discovery"/);
    assert.match(route, /recordUsageEvent\(\s*"generated_hypotheses"/);
    assert.match(route, /status: "queued"/);
    assert.match(route, /executeProductRunWorker\(\{ context, supabase, project, run, options \}\)/);
    assert.match(worker, /status: "running"/);
    assert.match(worker, /status: "succeeded"/);
    assert.match(worker, /status: "failed"/);
    assert.match(worker, /status: "partially_succeeded"/);
    assert.match(worker, /withRunTimeout/);
    assert.match(worker, /safeEngineError\(error\)/);
    assert.match(runSafety, /acknowledgement !== true/);
    assert.match(runSafety, /Choose mocked, dry_run, or an enabled read_only_live workflow mode/);
    assert.match(runSafety, /max_generated_hypotheses/);
    assert.match(runSafety, /externalWritesEnabled: false/);
    assert.match(runSafety, /antibodyGenerationEnabled: false/);
    assert.match(permissions, /researcher: \[[\s\S]*"run:create"/);
    assert.doesNotMatch(permissions.match(/viewer: \[[^\]]+\]/s)?.[0] ?? "", /run:create/);
  });

  it("status, cancel, result bundle, and artifact detail routes expose only product-safe state", () => {
    const statusRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts");
    const cancelRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/cancel/route.ts");
    const resultBundleRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts");
    const artifactDetailRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/[artifactId]/route.ts");

    assert.match(statusRoute, /status, progress, error_summary, result_summary/);
    assert.match(statusRoute, /requireProductPermission\("run:read", supabase\)/);
    assert.match(statusRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(cancelRoute, /requireProductPermission\("run:create", supabase\)/);
    assert.match(cancelRoute, /currentRun\.status !== "queued" && currentRun\.status !== "running"/);
    assert.match(cancelRoute, /status: "cancelled"/);
    assert.match(cancelRoute, /Active subprocess termination is not implemented in the V0\.3 dev runner/);
    assert.match(resultBundleRoute, /listRunArtifacts\(\{ \.\.\.context, supabase, projectId, runId \}, runId\)/);
    assert.match(resultBundleRoute, /artifact_type === "result_bundle_json"/);
    assert.match(artifactDetailRoute, /getRunArtifact\(\{ \.\.\.context, supabase, projectId, runId \}, artifactId\)/);

    for (const source of [statusRoute, cancelRoute, resultBundleRoute, artifactDetailRoute]) {
      assert.doesNotMatch(source, /stdout|stderr|stack|raw logs|raw Codex|AgentGraph|service_role/i);
    }
  });
});
