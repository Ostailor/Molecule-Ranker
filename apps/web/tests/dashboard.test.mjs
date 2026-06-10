import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("dashboard page", () => {
  it("loads own organization projects from Supabase", () => {
    const page = read("src/app/dashboard/page.tsx");
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(page, /requireUser\("\/login\?next=\/dashboard"\)/);
    assert.match(page, /\.from\("product_memberships"\)/);
    assert.match(page, /\.from\("product_organizations"\)/);
    assert.match(page, /\.from\("product_projects"\)/);
    assert.match(page, /\.eq\("organization_id", organization\.id\)/);
    assert.match(page, /\.order\("updated_at", \{ ascending: false \}\)/);
    assert.match(dashboard, /Tenant-scoped data/);
    assert.match(dashboard, /projects\.map/);
    assert.doesNotMatch(dashboard, /import \{[^}]*projects[^}]*\} from "@\/lib\/mock-data"/);
  });

  it("loads recent organization runs from Supabase", () => {
    const page = read("src/app/dashboard/page.tsx");
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(page, /\.from\("product_runs"\)/);
    assert.match(page, /\.eq\("organization_id", organization\.id\)/);
    assert.match(page, /\.order\("created_at", \{ ascending: false \}\)/);
    assert.match(page, /recentRuns=\{recentRuns\}/);
    assert.match(dashboard, /type DashboardRun/);
    assert.match(dashboard, /RecentDiscoveryRuns/);
    assert.match(dashboard, /Tenant-scoped runs/);
    assert.match(dashboard, /\/projects\/\$\{run\.projectId\}\/runs\/\$\{run\.id\}/);
    assert.match(dashboard, /Result bundle/);
    assert.doesNotMatch(dashboard, /resultBundles|runs \} from "@\/lib\/mock-data"/);
  });

  it("renders an empty projects state", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(dashboard, /EmptyProjectsState/);
    assert.match(dashboard, /No projects yet/);
    assert.match(dashboard, /Create project/);
    assert.match(dashboard, /projects\.length === 0/);
  });

  it("keeps the research-use disclaimer visible", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(dashboard, /ResearchUseBanner/);
    assert.match(dashboard, /Research-use reminder/);
    assert.match(dashboard, /requires expert review/i);
  });

  it("shows setup issue and account status pages before tenant data", () => {
    const page = read("src/app/dashboard/page.tsx");
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(page, /if \(!membership\)/);
    assert.match(page, /DashboardSetupIssuePage/);
    assert.match(page, /organization\.status !== "active"/);
    assert.match(page, /DashboardAccountStatusPage/);
    assert.match(dashboard, /Workspace setup needs attention/);
    assert.match(dashboard, /Account status limits dashboard access/);
  });

  it("documents cross-org project and run isolation through scoped query and RLS expectation", () => {
    const page = read("src/app/dashboard/page.tsx");
    const rlsDocs = read("../../docs/product/v0_2_rls_policies.md");
    const migration = read("../../supabase/migrations/0001_product_auth_schema.sql");
    const runsMigration = read("../../supabase/migrations/0002_product_discovery_runs.sql");

    assert.match(page, /\.eq\("organization_id", organization\.id\)/);
    assert.match(page, /product_projects/);
    assert.match(page, /product_runs/);
    assert.match(migration, /projects_select_for_members/);
    assert.match(migration, /public\.is_org_member\(organization_id\)/);
    assert.match(runsMigration, /runs_select_for_org_members/);
    assert.match(rlsDocs, /cannot read or write\s+rows outside organizations/i);
  });

  it("renders empty recent run state and create-run CTA", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(dashboard, /No recent discovery runs/);
    assert.match(dashboard, /Start run/);
    assert.match(dashboard, /\/projects\/\$\{firstProject\.id\}\/runs\/new/);
    assert.match(dashboard, /recentRuns\.length > 0/);
  });
});
