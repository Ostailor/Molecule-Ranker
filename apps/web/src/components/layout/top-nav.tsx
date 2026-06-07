import { Bell, ChevronDown } from "lucide-react";
import { projects } from "@/lib/mock-data";
import { Logo } from "@/components/layout/logo";
import { MobileNav } from "@/components/layout/mobile-nav";
import { AccountMenuPlaceholder } from "@/components/layout/account-menu-placeholder";

export function TopNav() {
  const activeProject = projects[0];

  return (
    <header className="sticky top-0 z-20 border-b border-slatewash-200 bg-white/92 backdrop-blur">
      <div className="flex h-16 items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <MobileNav />
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="hidden min-w-0 items-center gap-2 rounded-product border border-slatewash-200 bg-slatewash-50 px-3 py-2 md:flex">
            <span className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-400">Project</span>
            <span className="truncate text-sm font-semibold text-ink-950">{activeProject.name}</span>
            <ChevronDown className="h-4 w-4 text-ink-400" aria-hidden="true" />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="focus-ring hidden h-9 w-9 items-center justify-center rounded-product border border-slatewash-200 text-ink-600 transition hover:bg-slatewash-50 sm:flex">
            <Bell className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">Notifications</span>
          </button>
          <AccountMenuPlaceholder />
        </div>
      </div>
    </header>
  );
}
