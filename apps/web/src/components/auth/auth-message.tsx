"use client";

export function AuthMessage({ message, status }: { message?: string; status?: "error" | "success" | "idle" }) {
  if (!message) return null;

  const tone =
    status === "success"
      ? "border-teal-450/40 bg-teal-450/10 text-ink-800"
      : "border-rose-200 bg-rose-50 text-rose-700";

  return (
    <p aria-live="polite" className={`rounded-product border p-3 text-sm leading-6 ${tone}`}>
      {message}
    </p>
  );
}
