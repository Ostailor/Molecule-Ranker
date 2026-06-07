import { clsx } from "clsx";

export type TableColumn = {
  key: string;
  header: React.ReactNode;
};

export type TableRow = {
  id: string;
  cells: Record<string, React.ReactNode>;
};

export function Table({
  columns,
  rows,
  caption,
  className,
}: {
  columns: TableColumn[];
  rows: TableRow[];
  caption?: string;
  className?: string;
}) {
  return (
    <div className={clsx("overflow-x-auto rounded-product border border-slatewash-200", className)}>
      <table className="w-full min-w-full border-collapse text-left text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead className="bg-slatewash-50 text-xs font-semibold uppercase tracking-[0.08em] text-ink-600">
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className="border-b border-slatewash-200 px-3 py-3">
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slatewash-200 bg-white">
          {rows.map((row) => (
            <tr key={row.id} className="hover:bg-slatewash-50/70">
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-3 text-ink-800">
                  {row.cells[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
