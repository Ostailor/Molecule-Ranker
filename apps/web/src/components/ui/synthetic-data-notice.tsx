import { Alert } from "@/components/ui/alert";

export function SyntheticDataNotice({
  text = "This V0.1 page uses synthetic UI data. Real source-backed data will be connected in a later release.",
}: {
  text?: string;
}) {
  return (
    <Alert title="Synthetic data notice" tone="warning">
      {text}
    </Alert>
  );
}
