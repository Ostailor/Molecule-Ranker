export function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

export function compactNumber(value: number) {
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function scoreTone(score: number) {
  if (score >= 0.82) return "text-teal-700 bg-teal-450/10 border-teal-450/20";
  if (score >= 0.68) return "text-amber-700 bg-amber-450/10 border-amber-450/25";
  return "text-rose-700 bg-rose-50 border-rose-200";
}
