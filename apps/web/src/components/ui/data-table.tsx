import { clsx } from "clsx";

export function DataTable({
  columns,
  rows,
  className,
}: {
  columns: string[];
  rows: React.ReactNode[][];
  className?: string;
}) {
  return (
    <div className={clsx("overflow-hidden rounded-product border border-slatewash-200", className)}>
      <table className="w-full border-collapse text-left text-sm">
        <thead className="bg-slatewash-50 text-xs font-semibold uppercase tracking-[0.08em] text-ink-600">
          <tr>
            {columns.map((column) => (
              <th key={column} className="border-b border-slatewash-200 px-3 py-3">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slatewash-200 bg-white">
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="hover:bg-slatewash-50/70">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-3 py-3 text-ink-800">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
