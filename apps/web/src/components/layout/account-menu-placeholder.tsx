import Link from "next/link";

export function AccountMenuPlaceholder() {
  return (
    <Link
      href="/account"
      className="focus-ring flex h-9 items-center gap-2 rounded-product border border-slatewash-200 bg-white px-2 text-sm font-semibold text-ink-800"
      aria-label="Open account settings"
    >
      <span className="flex h-6 w-6 items-center justify-center rounded-product bg-teal-450/10 text-xs text-teal-700">
        OR
      </span>
      <span className="hidden sm:inline">Research Ops</span>
    </Link>
  );
}
