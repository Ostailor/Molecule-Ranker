import { FeedbackFormMock } from "@/components/feedback/feedback-form-mock";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { feedbackMessages } from "@/lib/mock-data";

export default function FeedbackPage() {
  return (
    <AppShell>
      <PageHeader title="Feedback" description="Capture product feedback locally in the mock UI." />
      <div className="grid gap-6 xl:grid-cols-[1fr_0.8fr]">
        <FeedbackFormMock />
        <Card>
          <CardHeader title="Recent feedback messages" eyebrow="Synthetic examples" />
          <CardBody>
            <div className="grid gap-3">
              {feedbackMessages.map((message) => (
                <article key={message.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.1em] text-teal-700">{message.category}</p>
                  <p className="mt-2 text-sm leading-6 text-ink-700">{message.message}</p>
                  <p className="mt-2 text-xs font-semibold text-ink-500">{message.status}</p>
                </article>
              ))}
            </div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
