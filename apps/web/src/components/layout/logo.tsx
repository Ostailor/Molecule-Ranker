import Link from "next/link";
import { Hexagon } from "lucide-react";

export function Logo() {
  return (
    <Link href="/" className="focus-ring flex items-center gap-2 rounded-product">
      <span className="flex h-9 w-9 items-center justify-center rounded-product bg-ink-950 text-lime-350">
        <Hexagon className="h-5 w-5" aria-hidden="true" />
      </span>
      <span>
        <span className="block text-sm font-semibold leading-4 text-ink-950">MolCreate</span>
        <span className="block text-[11px] font-medium leading-4 text-ink-600">Discovery OS</span>
      </span>
    </Link>
  );
}
