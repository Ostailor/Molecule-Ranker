import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
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

const roles = {
  owner: ["project:create", "project:read", "run:create", "run:read", "admin:read", "feedback:create"],
  admin: ["project:create", "project:read", "run:create", "run:read", "admin:read", "feedback:create"],
  researcher: ["project:create", "project:read", "run:create", "run:read", "feedback:create"],
  viewer: ["project:read", "run:read", "feedback:create"],
};

const fixtures = {
  projects: [
    { id: "project-a", organization_id: "org-a", name: "Org A project", created_by_user_id: "user-a" },
    { id: "project-b", organization_id: "org-b", name: "Org B project", created_by_user_id: "user-b" },
  ],
  feedback: [
    { id: "feedback-a", organization_id: "org-a", user_id: "user-a", message: "Org A feedback" },
    { id: "feedback-b", organization_id: "org-b", user_id: "user-b", message: "Org B feedback" },
  ],
  usageEvents: [
    { id: "usage-a", organization_id: "org-a", user_id: "user-a", event_type: "create_project", quantity: 1 },
    { id: "usage-b", organization_id: "org-b", user_id: "user-b", event_type: "feedback_create", quantity: 1 },
  ],
  runs: [
    { id: "run-a", organization_id: "org-a", project_id: "project-a", created_by_user_id: "user-a" },
    { id: "run-b", organization_id: "org-b", project_id: "project-b", created_by_user_id: "user-b" },
  ],
  artifacts: [
    { id: "artifact-a", organization_id: "org-a", project_id: "project-a", run_id: "run-a" },
    { id: "artifact-b", organization_id: "org-b", project_id: "project-b", run_id: "run-b" },
  ],
};

function context({ orgId = "org-a", role = "researcher", userId = "user-a" } = {}) {
  return {
    authenticated: true,
    user: { id: userId },
    organization: { id: orgId, name: orgId === "org-a" ? "Org A" : "Org B" },
    role,
  };
}

function requireAuth(ctx) {
  if (!ctx?.authenticated) {
    return { status: 401, code: "UNAUTHENTICATED" };
  }

  return null;
}

function hasPermission(role, permission) {
  return roles[role]?.includes(permission) ?? false;
}

function listProjects(ctx) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "project:read")) return { status: 403, code: "FORBIDDEN" };

  return {
    status: 200,
    projects: fixtures.projects.filter((project) => project.organization_id === ctx.organization.id),
  };
}

function getProject(ctx, projectId) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "project:read")) return { status: 403, code: "FORBIDDEN" };

  const project = fixtures.projects.find(
    (item) => item.id === projectId && item.organization_id === ctx.organization.id,
  );
  return project ? { status: 200, project } : { status: 404, code: "NOT_FOUND" };
}

function createProject(ctx, project) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "project:create")) return { status: 403, code: "FORBIDDEN" };

  return {
    status: 201,
    project: {
      ...project,
      organization_id: ctx.organization.id,
      created_by_user_id: ctx.user.id,
    },
  };
}

function adminSummary(ctx) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "admin:read")) return { status: 403, code: "FORBIDDEN" };

  return {
    status: 200,
    summary: {
      organizationId: ctx.organization.id,
      projectCount: fixtures.projects.filter((project) => project.organization_id === ctx.organization.id).length,
      usageEventCount: fixtures.usageEvents.filter((event) => event.organization_id === ctx.organization.id).length,
    },
  };
}

function listFeedback(ctx) {
  const authError = requireAuth(ctx);
  if (authError) return authError;

  return fixtures.feedback.filter((feedback) => feedback.organization_id === ctx.organization.id);
}

function listUsageEvents(ctx) {
  const authError = requireAuth(ctx);
  if (authError) return authError;

  return fixtures.usageEvents.filter((event) => event.organization_id === ctx.organization.id);
}

function listRuns(ctx, projectId) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "run:read")) return { status: 403, code: "FORBIDDEN" };

  return fixtures.runs.filter((run) => run.organization_id === ctx.organization.id && run.project_id === projectId);
}

function listArtifacts(ctx, runId) {
  const authError = requireAuth(ctx);
  if (authError) return authError;
  if (!hasPermission(ctx.role, "run:read")) return { status: 403, code: "FORBIDDEN" };

  return fixtures.artifacts.filter((artifact) => artifact.organization_id === ctx.organization.id && artifact.run_id === runId);
}

describe("tenant isolation with mocked Supabase data", () => {
  it("User A in Org A cannot see Org B projects", () => {
    const result = listProjects(context());

    assert.equal(result.status, 200);
    assert.deepEqual(result.projects.map((project) => project.id), ["project-a"]);
    assert.ok(result.projects.every((project) => project.organization_id === "org-a"));
  });

  it("User A in Org A cannot fetch Org B project detail", () => {
    assert.deepEqual(getProject(context(), "project-b"), { status: 404, code: "NOT_FOUND" });
  });

  it("viewer cannot create project", () => {
    assert.deepEqual(createProject(context({ role: "viewer" }), { id: "new-project" }), {
      status: 403,
      code: "FORBIDDEN",
    });
  });

  it("researcher can create project in own org", () => {
    const result = createProject(context({ role: "researcher" }), { id: "new-project", name: "New project" });

    assert.equal(result.status, 201);
    assert.equal(result.project.organization_id, "org-a");
    assert.equal(result.project.created_by_user_id, "user-a");
  });

  it("researcher cannot access admin summary", () => {
    assert.deepEqual(adminSummary(context({ role: "researcher" })), { status: 403, code: "FORBIDDEN" });
  });

  it("admin can access admin summary for own org", () => {
    const result = adminSummary(context({ role: "admin" }));

    assert.equal(result.status, 200);
    assert.deepEqual(result.summary, {
      organizationId: "org-a",
      projectCount: 1,
      usageEventCount: 1,
    });
  });

  it("feedback is scoped to org", () => {
    const rows = listFeedback(context());

    assert.deepEqual(rows.map((row) => row.id), ["feedback-a"]);
    assert.ok(rows.every((row) => row.organization_id === "org-a"));
  });

  it("usage events are scoped to org", () => {
    const rows = listUsageEvents(context());

    assert.deepEqual(rows.map((row) => row.id), ["usage-a"]);
    assert.ok(rows.every((row) => row.organization_id === "org-a"));
  });

  it("runs and artifacts are scoped to org", () => {
    const runs = listRuns(context(), "project-a");
    const artifacts = listArtifacts(context(), "run-a");

    assert.deepEqual(runs.map((row) => row.id), ["run-a"]);
    assert.deepEqual(artifacts.map((row) => row.id), ["artifact-a"]);
    assert.deepEqual(listRuns(context(), "project-b"), []);
    assert.deepEqual(listArtifacts(context(), "run-b"), []);
  });

  it("unauthenticated user cannot access product API", () => {
    assert.deepEqual(listProjects(null), { status: 401, code: "UNAUTHENTICATED" });
    assert.deepEqual(adminSummary({ authenticated: false }), { status: 401, code: "UNAUTHENTICATED" });
  });

  it("service role key is not referenced in client code", () => {
    const findings = sourceFiles()
      .map((file) => [file, readFileSync(file, "utf8")])
      .filter(([, source]) => /SUPABASE_SERVICE_ROLE_KEY|service_role|serviceRole/.test(source))
      .map(([file]) => relative(root, file));

    assert.deepEqual(findings, []);
  });

  it("real product routes encode the same tenant and role boundaries", () => {
    const projectsRoute = read("src/app/api/product/projects/route.ts");
    const projectDetailRoute = read("src/app/api/product/projects/[projectId]/route.ts");
    const adminSummaryRoute = read("src/app/api/product/admin/summary/route.ts");
    const feedbackRoute = read("src/app/api/product/feedback/route.ts");
    const usageRoute = read("src/app/api/product/usage/route.ts");
    const runsRoute = read("src/app/api/product/projects/[projectId]/runs/route.ts");
    const runRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/route.ts");
    const artifactsRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/artifacts/route.ts");
    const resultRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/result/route.ts");
    const artifactStorage = read("src/lib/product/artifact-storage.ts");
    const usageHelper = read("src/lib/product/usage.ts");
    const permissions = read("src/lib/product/permissions.ts");

    assert.match(projectsRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(projectsRoute, /requireProductPermission\("project:create", supabase\)/);
    assert.match(projectDetailRoute, /\.eq\("id", projectId\)/);
    assert.match(projectDetailRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(adminSummaryRoute, /requireAdminRole\(supabase\)/);
    assert.match(adminSummaryRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(feedbackRoute, /organization_id: context\.organization\.id/);
    assert.match(usageRoute, /getUsageSummaryForOrg\(context\.organization\.id/);
    assert.match(runsRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(runRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(artifactsRoute, /listRunArtifacts\(\{ \.\.\.context, supabase, projectId, runId \}, runId\)/);
    assert.match(resultRoute, /listRunArtifacts\(\{ \.\.\.context, supabase, projectId, runId \}, runId\)/);
    assert.match(artifactStorage, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(artifactStorage, /artifactVisibilityFilter/);
    assert.match(usageHelper, /context\.organization\.id !== orgId/);
    assert.match(permissions, /viewer: \["project:read", "run:read", "feedback:create"\]/);
    assert.doesNotMatch(permissions.match(/researcher: \[[^\]]+\]/s)?.[0] ?? "", /admin:read/);
  });
});
