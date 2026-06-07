import { percent } from "@/lib/formatting";
import { Badge } from "@/components/ui/badge";

export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 0.82 ? "teal" : score >= 0.68 ? "amber" : "rose";
  return <Badge tone={tone}>{percent(score)}</Badge>;
}
