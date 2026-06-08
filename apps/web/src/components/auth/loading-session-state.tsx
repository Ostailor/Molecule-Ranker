import { LoaderCircle } from "lucide-react";

export function LoadingSessionState() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="flex items-center gap-3 rounded-product border border-slatewash-200 bg-white px-4 py-3 text-sm font-semibold text-ink-700 shadow-line">
        <LoaderCircle className="h-4 w-4 animate-spin text-teal-700" aria-hidden="true" />
        <span>Checking session</span>
      </div>
    </main>
  );
}
