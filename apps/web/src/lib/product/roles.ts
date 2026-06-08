import type { ProductRole } from "./types";

export const productRoles = ["owner", "admin", "researcher", "viewer"] as const satisfies readonly ProductRole[];

export function isProductRole(value: string): value is ProductRole {
  return productRoles.includes(value as ProductRole);
}

export function roleRank(role: ProductRole) {
  switch (role) {
    case "owner":
      return 40;
    case "admin":
      return 30;
    case "researcher":
      return 20;
    case "viewer":
      return 10;
  }
}
