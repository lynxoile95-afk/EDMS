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
export function Tabs({
  tabs,
  initial,
  active: controlled,
  onActiveChange,
}: {
  tabs: TabDef[];
  initial?: string;
  /** Controlled mode — lets a ribbon button switch tabs. */
  active?: string;
  onActiveChange?: (key: string) => void;
}) {
  const [internal, setInternal] = useState(initial ?? tabs[0]?.key);
  const active = controlled ?? internal;
  const setActive = (key: string) => {
    setInternal(key);
    onActiveChange?.(key);
  };
  const current = tabs.find((tab) => tab.key === active) ?? tabs[0];
  if (!current) return null;

  return (
    <div>
      <div className="tabstrip" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={tab.key === current.key}
            className={clsx("tab", tab.key === current.key && "is-active")}
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
