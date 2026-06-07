import type { ConfidenceBand } from "@/lib/mock-data";
import { Badge } from "@/components/ui/badge";

export function ConfidenceBadge({ confidence }: { confidence: ConfidenceBand }) {
  const tone = confidence === "High" ? "green" : confidence === "Medium" ? "amber" : "gray";
  return <Badge tone={tone}>{confidence}</Badge>;
}
