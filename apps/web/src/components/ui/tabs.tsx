"use client";

import { useId, useState } from "react";
import { clsx } from "clsx";

export type TabItem = {
  id: string;
  label: string;
  panel: React.ReactNode;
};

export function Tabs({
  tabs,
  defaultTabId,
  ariaLabel = "Tabs",
}: {
  tabs: TabItem[];
  defaultTabId?: string;
  ariaLabel?: string;
}) {
  const baseId = useId();
  const [activeId, setActiveId] = useState(defaultTabId ?? tabs[0]?.id);
  const activeTab = tabs.find((tab) => tab.id === activeId) ?? tabs[0];

  if (!activeTab) return null;

  return (
    <div>
      <div role="tablist" aria-label={ariaLabel} className="flex flex-wrap gap-2 border-b border-slatewash-200">
        {tabs.map((tab) => {
          const selected = tab.id === activeTab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`${baseId}-${tab.id}-tab`}
              aria-selected={selected}
              aria-controls={`${baseId}-${tab.id}-panel`}
              onClick={() => setActiveId(tab.id)}
              className={clsx(
                "focus-ring -mb-px rounded-t-product border border-transparent px-3 py-2 text-sm font-semibold transition",
                selected
                  ? "border-slatewash-200 border-b-white bg-white text-ink-950"
                  : "text-ink-600 hover:bg-slatewash-50 hover:text-ink-950",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`${baseId}-${activeTab.id}-panel`}
        aria-labelledby={`${baseId}-${activeTab.id}-tab`}
        className="pt-4"
      >
        {activeTab.panel}
      </div>
    </div>
  );
}
