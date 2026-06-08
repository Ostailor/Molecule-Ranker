import Link from "next/link";
import { Menu } from "lucide-react";
import { adminNav, productNav, supportNav } from "@/lib/routes";
import { canShowAdminDashboard, productFeatureFlags } from "@/lib/product/feature-flags";
import type { ProductRole } from "@/lib/product/types";

export function MobileNav({ userRole }: { userRole?: ProductRole | null }) {
  const visibleProductNav = productNav.filter((item) => {
    return item.feature ? productFeatureFlags[item.feature] : true;
  });
  const showAdminNav = canShowAdminDashboard(userRole);

  return (
    <details className="relative lg:hidden">
      <summary className="focus-ring flex h-9 w-9 cursor-pointer list-none items-center justify-center rounded-product border border-slatewash-200 text-ink-600 marker:hidden">
        <Menu className="h-4 w-4" aria-hidden="true" />
        <span className="sr-only">Open navigation</span>
      </summary>
      <div className="absolute left-0 top-11 z-30 w-72 rounded-product border border-slatewash-200 bg-white p-2 shadow-soft">
        <nav className="space-y-1" aria-label="Mobile navigation">
          {[...visibleProductNav, ...supportNav].map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="focus-ring flex h-10 items-center gap-3 rounded-product px-3 text-sm font-medium text-ink-700 transition hover:bg-slatewash-50 hover:text-ink-950"
            >
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </Link>
          ))}
        </nav>
        {showAdminNav ? (
          <div className="mt-2 border-t border-slatewash-200 pt-2">
            {adminNav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="focus-ring flex h-10 items-center justify-between rounded-product px-3 text-sm font-medium text-ink-500 transition hover:bg-slatewash-50"
              >
                <span className="flex items-center gap-3">
                  <item.icon className="h-4 w-4" aria-hidden="true" />
                  {item.label}
                </span>
                <span className="rounded-product border border-slatewash-200 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.1em] text-ink-400">
                  Admin only
                </span>
              </Link>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}
