import { evidenceItems } from "@/lib/mock-data";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";

export function EvidencePanel() {
  return (
    <Card>
      <CardHeader title="Evidence trace" eyebrow="Synthetic demo rows" />
      <CardBody>
        <DataTable
          columns={["Source", "Title", "Summary", "Confidence", "Synthetic"]}
          rows={evidenceItems.map((record) => [
            <span key={`${record.id}-source`} className="font-semibold text-ink-950">
              {record.sourceType}
            </span>,
            record.title,
            <span key={`${record.id}-summary`} className="block max-w-2xl text-sm leading-6">
              {record.summary}
            </span>,
            record.confidence,
            <StatusBadge key={`${record.id}-synthetic`} tone="amber">
              {record.synthetic ? "Synthetic" : "Review"}
            </StatusBadge>,
          ])}
        />
      </CardBody>
    </Card>
  );
}
