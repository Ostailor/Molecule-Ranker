import Link from "next/link";
import { adminNav, productNav, supportNav } from "@/lib/routes";
import { Logo } from "@/components/layout/logo";
import { canShowAdminDashboard, productFeatureFlags } from "@/lib/product/feature-flags";
import type { ProductRole } from "@/lib/product/types";

export function SideNav({ userRole }: { userRole?: ProductRole | null }) {
  const visibleProductNav = productNav.filter((item) => {
    return item.feature ? productFeatureFlags[item.feature] : true;
  });
  const showAdminNav = canShowAdminDashboard(userRole);

  return (
    <aside className="hidden min-h-screen w-64 shrink-0 border-r border-slatewash-200 bg-white px-4 py-5 lg:block">
      <Logo />
      <nav className="mt-8 space-y-1" aria-label="Primary navigation">
        {visibleProductNav.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="focus-ring flex h-10 items-center gap-3 rounded-product px-3 text-sm font-medium text-ink-600 transition hover:bg-slatewash-50 hover:text-ink-950"
          >
            <item.icon className="h-4 w-4" aria-hidden="true" />
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="mt-8 border-t border-slatewash-200 pt-4">
        <nav className="space-y-1" aria-label="Support navigation">
          {supportNav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="focus-ring flex h-10 items-center gap-3 rounded-product px-3 text-sm font-medium text-ink-600 transition hover:bg-slatewash-50 hover:text-ink-950"
            >
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
      {showAdminNav ? (
        <div className="mt-6 border-t border-slatewash-200 pt-4">
          <p className="px-3 text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Admin-only</p>
          <nav className="mt-2 space-y-1" aria-label="Admin navigation">
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
                  Placeholder
                </span>
              </Link>
            ))}
          </nav>
        </div>
      ) : null}
      <div className="mt-8 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Workspace mode</p>
        <p className="mt-2 text-sm font-semibold text-ink-950">Mock frontend</p>
        <p className="mt-1 text-xs leading-5 text-ink-600">Mock-only workspace for local product review.</p>
      </div>
    </aside>
  );
}
