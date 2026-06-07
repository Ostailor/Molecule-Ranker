import { AlertTriangle } from "lucide-react";

export function WarningList({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) {
    return <p className="text-sm text-ink-600">No warnings listed.</p>;
  }

  return (
    <ul className="grid gap-2 text-sm leading-6 text-ink-700">
      {warnings.map((warning) => (
        <li key={warning} className="flex gap-2">
          <AlertTriangle className="mt-1 h-3.5 w-3.5 shrink-0 text-amber-700" aria-hidden="true" />
          <span>{warning}</span>
        </li>
      ))}
    </ul>
  );
}
