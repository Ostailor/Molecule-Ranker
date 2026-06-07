import { ShieldAlert } from "lucide-react";
import { researchUseDisclaimer } from "@/lib/disclaimers";

export function DisclaimerBanner({ text = researchUseDisclaimer }: { text?: string }) {
  return (
    <div className="rounded-product border border-amber-450/25 bg-amber-450/10 p-3 text-sm text-ink-800">
      <div className="flex gap-3">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
        <p className="font-semibold text-ink-950">{text}</p>
      </div>
    </div>
  );
}
