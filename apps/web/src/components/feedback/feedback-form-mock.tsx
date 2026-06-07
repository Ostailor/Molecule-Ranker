"use client";

import { useState } from "react";
import { MessageSquareText, Send } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export function FeedbackFormMock() {
  const [submitted, setSubmitted] = useState(false);

  return (
    <Card>
      <CardHeader title="Feedback form mock" eyebrow="PLACEHOLDER_V0_1_FEEDBACK" />
      <CardBody>
        <form className="grid gap-4">
          <label>
            <span className="text-sm font-semibold text-ink-800">Category</span>
            <select
              name="category"
              className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
              defaultValue="result-bundle"
            >
              <option value="dashboard">Dashboard</option>
              <option value="projects">Projects</option>
              <option value="runs">Discovery runs</option>
              <option value="result-bundle">Result bundle</option>
              <option value="candidates">Candidates</option>
              <option value="evidence">Evidence</option>
              <option value="generated">Generated hypotheses</option>
            </select>
          </label>

          <label>
            <span className="text-sm font-semibold text-ink-800">Message</span>
            <textarea
              name="message"
              className="mt-2 min-h-36 w-full rounded-product border-slatewash-200 text-sm leading-6 focus:border-teal-550 focus:ring-teal-550"
              defaultValue="The evidence coverage view should make limitations easier to scan during research review."
            />
          </label>

          <label>
            <span className="text-sm font-semibold text-ink-800">Contact email placeholder</span>
            <input
              name="contact-email"
              type="email"
              className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
              defaultValue="pilot@example.test"
            />
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setSubmitted(true)}
              className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border border-teal-550 bg-teal-550 px-3 text-sm font-semibold text-white transition hover:bg-teal-700"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              <span>Save mock feedback</span>
            </button>
            <p className="text-sm text-ink-600">No backend submit yet.</p>
          </div>

          {submitted ? (
            <div className="flex items-start gap-3 rounded-product border border-teal-450/40 bg-teal-450/10 p-3">
              <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" aria-hidden="true" />
              <p className="text-sm leading-6 text-ink-700">
                Feedback saved locally for this mock session. No message was sent and no backend request was made.
              </p>
            </div>
          ) : null}
        </form>
      </CardBody>
    </Card>
  );
}
