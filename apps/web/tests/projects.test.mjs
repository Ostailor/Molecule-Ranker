import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("project pages", () => {
  it("researcher, admin, and owner can create projects through Supabase", () => {
    const page = read("src/app/projects/new/page.tsx");
    const form = read("src/components/projects/create-project-form.tsx");
    const actions = read("src/lib/supabase/project-actions.ts");

    for (const label of ["Project name", "Research goal", "Disease or research area", "Optional target focus"]) {
      assert.match(`${page}\n${form}`, new RegExp(label));
    }

    assert.match(page, /requireUser\("\/login\?next=\/projects\/new"\)/);
    assert.match(page, /canCreateProject\(role\)/);
    assert.match(actions, /canCreateProject\(role\)/);
    assert.match(actions, /\.from\("product_projects"\)/);
    assert.match(actions, /organization_id: membership\.organization_id/);
    assert.match(actions, /created_by_user_id: user\.id/);
    assert.match(actions, /checkUsageAllowed\("create_project", 1, \{ supabaseClient: supabase \}\)/);
    assert.match(actions, /recordUsageEvent\("create_project", 1, \{ project_id: data\.id \}/);
    assert.match(actions, /redirect\(`\/projects\/\$\{data\.id\}`\)/);
  });

  it("viewer cannot create project", () => {
    const page = read("src/app/projects/new/page.tsx");
    const actions = read("src/lib/supabase/project-actions.ts");
    const permissions = read("src/lib/product/permissions.ts");

    assert.match(permissions, /viewer: \["project:read", "run:read", "feedback:create"\]/);
    assert.match(page, /if \(!canCreateProject\(role\)\)/);
    assert.match(page, /ForbiddenPage/);
    assert.match(actions, /Your current role can view projects but cannot create them/);
  });

  it("project detail loads only same-org project data", () => {
    const page = read("src/app/projects/[projectId]/page.tsx");
    const migration = read("../../supabase/migrations/0001_product_auth_schema.sql");

    for (const text of [
      "requireUser",
      "product_memberships",
      "product_projects",
      ".eq(\"id\", projectId)",
      ".eq(\"organization_id\", membership.organization_id)",
      "Project summary",
      "Recent runs",
      "Start new discovery run",
      "Result bundles",
    ]) {
      assert.match(page, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    assert.match(migration, /projects_select_for_members/);
    assert.match(migration, /public\.is_org_member\(organization_id\)/);
  });

  it("unsafe-request warning appears on create project page", () => {
    const source = `${read("src/app/projects/new/page.tsx")}\n${read("src/components/projects/create-project-form.tsx")}`;

    assert.match(source, /Do not enter patient-specific or protected health information\./);
    assert.match(source, /Do not request treatment, dosing, synthesis, or lab protocols\./);
    assert.match(source, /maxLength=\{120\}/);
    assert.match(source, /maxLength=\{1000\}/);
    assert.match(source, /maxLength=\{160\}/);
    assert.match(source, /name="name"/);
    assert.match(source, /name="research_goal"/);
    assert.match(source, /name="disease_focus"/);
    assert.match(source, /name="target_focus"/);
  });

  it("project not found safe state does not expose cross-org data", () => {
    const page = read("src/app/projects/[projectId]/page.tsx");

    assert.match(page, /ProjectNotFound/);
    assert.match(page, /No accessible project matches this identifier/);
    assert.match(page, /No cross-organization\s+project data is shown/);
    assert.match(page, /if \(!isUuid\(projectId\)\) return <ProjectNotFound/);
    assert.match(page, /if \(!project\) return <ProjectNotFound/);
  });

  it("runs and result bundles remain placeholders until later releases", () => {
    const page = read("src/app/projects/[projectId]/page.tsx");

    assert.match(page, /Placeholder until V0\.3/);
    assert.match(page, /Placeholder until V0\.3\/V0\.4/);
    assert.match(page, /No live workflow execution is started in V0\.2/);
  });
});
