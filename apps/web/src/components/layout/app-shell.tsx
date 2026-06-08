import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Footer } from "@/components/layout/footer";
import { SideNav } from "@/components/layout/side-nav";
import { TopNav } from "@/components/layout/top-nav";
import type { ProductRole } from "@/lib/product/types";

export function AppShell({ children, userRole }: { children: React.ReactNode; userRole?: ProductRole | null }) {
  return (
    <div className="flex min-h-screen bg-slatewash-50">
      <SideNav userRole={userRole} />
      <div className="min-w-0 flex-1">
        <TopNav userRole={userRole} />
        <div className="border-b border-amber-450/20 bg-white px-4 py-3 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1440px]">
            <ResearchUseBanner />
          </div>
        </div>
        <main className="mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        <Footer />
      </div>
    </div>
  );
}
