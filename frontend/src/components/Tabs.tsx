import clsx from "clsx";
import { useState } from "react";
import type { ReactNode } from "react";

export interface TabDef {
  key: string;
  label: string;
  /** Optional numeric badge shown next to the label (counts, unread, …). */
  count?: number;
  content: ReactNode;
}

/** Classic tab strip, in the order given (RTL reading order). */
export function Tabs({ tabs, initial }: { tabs: TabDef[]; initial?: string }) {
  const [active, setActive] = useState(initial ?? tabs[0]?.key);
  const current = tabs.find((tab) => tab.key === active) ?? tabs[0];
  if (!current) return null;

  return (
    <div>
      <div className="tab-strip" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={tab.key === current.key}
            className={clsx("tab-btn", tab.key === current.key && "is-active")}
            onClick={() => setActive(tab.key)}
          >
            {tab.label}
            {typeof tab.count === "number" ? (
              <span className="mr-1.5 text-[9px] text-[#1f3864]">({tab.count})</span>
            ) : null}
          </button>
        ))}
      </div>
      <div role="tabpanel">{current.content}</div>
    </div>
  );
}
