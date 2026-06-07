import type { LucideIcon } from "lucide-react";
import { Metric } from "@/components/ui/metric";

export function StatCard({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon?: LucideIcon;
}) {
  return <Metric label={label} value={value} detail={detail} icon={icon} />;
}
