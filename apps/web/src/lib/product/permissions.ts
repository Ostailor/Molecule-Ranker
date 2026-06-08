import { roleRank } from "./roles";
import { ProductAuthorizationError, type ProductPermission, type ProductRole } from "./types";

export const pilotPermissions = [
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
] as const satisfies readonly ProductPermission[];

export const permissionsByRole: Record<ProductRole, readonly ProductPermission[]> = {
  owner: pilotPermissions,
  admin: [
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
  ],
  researcher: [
    "project:create",
    "project:read",
    "project:update",
    "run:create",
    "run:read",
    "candidate:save",
    "export:create",
    "feedback:create",
  ],
  viewer: ["project:read", "run:read", "feedback:create"],
};

export function roleHasPermission(role: ProductRole, permission: ProductPermission) {
  return permissionsByRole[role].includes(permission);
}

export function requireRole(role: ProductRole, minimumRole: ProductRole) {
  if (roleRank(role) < roleRank(minimumRole)) {
    throw new ProductAuthorizationError(`Role ${role} does not satisfy required role ${minimumRole}.`);
  }
}

export function requirePermission(role: ProductRole, permission: ProductPermission) {
  if (!roleHasPermission(role, permission)) {
    throw new ProductAuthorizationError(`Role ${role} does not have permission ${permission}.`);
  }
}

export function canAccessAdmin(role: ProductRole) {
  return roleHasPermission(role, "admin:read");
}

export function canCreateProject(role: ProductRole) {
  return roleHasPermission(role, "project:create");
}

export function canViewProject(role: ProductRole) {
  return roleHasPermission(role, "project:read");
}
