import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

function permissionsForRole(source, role) {
  if (role === "owner") {
    const pilotMatch = source.match(/export const pilotPermissions = \[([\s\S]*?)\] as const/);
    assert.ok(pilotMatch, "pilotPermissions should be defined");
    return Array.from(pilotMatch[1].matchAll(/"([^"]+)"/g), (match) => match[1]);
  }

  const roleMatch = source.match(new RegExp(`${role}: \\[([\\s\\S]*?)\\]`, "m"));
  assert.ok(roleMatch, `${role} permissions should be defined`);
  return Array.from(roleMatch[1].matchAll(/"([^"]+)"/g), (match) => match[1]);
}

describe("product roles and permissions", () => {
  it("defines product-side types and roles", () => {
    const types = read("src/lib/product/types.ts");
    const roles = read("src/lib/product/roles.ts");

    for (const typeName of [
      "ProductProfile",
      "ProductOrganization",
      "ProductMembership",
      "ProductRole",
      "ProductProject",
      "ProductUsageEvent",
      "ProductFeedback",
      "ProductAuthContext",
    ]) {
      assert.match(types, new RegExp(`export type ${typeName}\\b`));
    }

    for (const role of ["owner", "admin", "researcher", "viewer"]) {
      assert.match(roles, new RegExp(`"${role}"`));
    }
  });

  it("owner has all pilot permissions", () => {
    const source = read("src/lib/product/permissions.ts");
    const ownerPermissions = permissionsForRole(source, "owner");

    for (const permission of [
      "project:create",
      "project:read",
      "project:update",
      "run:create",
      "run:read",
      "candidate:save",
      "export:create",
      "feedback:create",
      "admin:read",
      "admin:manage_users",
    ]) {
      assert.ok(ownerPermissions.includes(permission), `owner should have ${permission}`);
    }
  });

  it("admin can manage users", () => {
    const source = read("src/lib/product/permissions.ts");
    const adminPermissions = permissionsForRole(source, "admin");

    assert.ok(adminPermissions.includes("admin:manage_users"));
    assert.match(source, /canAccessAdmin/);
  });

  it("researcher can create project", () => {
    const source = read("src/lib/product/permissions.ts");
    const researcherPermissions = permissionsForRole(source, "researcher");

    assert.ok(researcherPermissions.includes("project:create"));
    assert.match(source, /canCreateProject/);
  });

  it("viewer cannot create project", () => {
    const source = read("src/lib/product/permissions.ts");
    const viewerPermissions = permissionsForRole(source, "viewer");

    assert.ok(!viewerPermissions.includes("project:create"));
    assert.ok(viewerPermissions.includes("project:read"));
  });

  it("normal users cannot access admin", () => {
    const source = read("src/lib/product/permissions.ts");

    assert.ok(!permissionsForRole(source, "researcher").includes("admin:read"));
    assert.ok(!permissionsForRole(source, "viewer").includes("admin:read"));
    assert.match(source, /roleHasPermission\(role, "admin:read"\)/);
  });

  it("exports authorization helpers", () => {
    const source = read("src/lib/product/permissions.ts");

    for (const helper of [
      "roleHasPermission",
      "requireRole",
      "requirePermission",
      "canAccessAdmin",
      "canCreateProject",
      "canViewProject",
    ]) {
      assert.match(source, new RegExp(`export function ${helper}\\b`));
    }
  });
});
