export function FeatureFlagGate({
  enabled,
  children,
  fallback = null,
}: {
  enabled: boolean;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  if (!enabled) return fallback;
  return <>{children}</>;
}
