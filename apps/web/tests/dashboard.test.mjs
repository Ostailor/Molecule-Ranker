import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("dashboard page", () => {
  it("renders mock projects from the synthetic dataset", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");
    const mockData = read("src/lib/mock-data.ts");

    assert.match(mockData, /ExampleDiseaseA hypothesis review/);
    assert.match(mockData, /ExampleDiseaseB result bundle rehearsal/);
    assert.match(dashboard, /projects\.map/);
    assert.match(dashboard, /Recent discovery runs/);
    assert.match(dashboard, /Saved candidates/);
  });

  it("renders an empty projects state", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");
    const page = read("src/app/dashboard/page.tsx");

    assert.match(dashboard, /EmptyProjectsState/);
    assert.match(dashboard, /No projects yet/);
    assert.match(dashboard, /Create project/);
    assert.match(page, /value === "empty"/);
  });

  it("keeps the research-use disclaimer visible", () => {
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");

    assert.match(dashboard, /ResearchUseBanner/);
    assert.match(dashboard, /Research-use reminder/);
    assert.match(dashboard, /requires expert review/i);
  });
});

