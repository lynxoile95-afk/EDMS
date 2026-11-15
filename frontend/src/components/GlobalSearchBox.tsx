import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { IconSearch } from "@/components/icons";

interface SearchStatus {
  enabled: boolean;
  backend: string;
  degraded: boolean;
  semantic_supported: boolean;
  message?: string | null;
}

/** The content-search control is shown only after the backend reports it on. */
export function GlobalSearchBox() {
  const navigate = useNavigate();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [value, setValue] = useState("");

  useEffect(() => {
    let active = true;
    api
      .get<SearchStatus>("/search/status")
      .then((status) => {
        if (active) setEnabled(status.enabled);
      })
      .catch(() => {
        if (active) setEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Preserve the pre-existing MDL/site search when the additive RAG feature is
  // disabled or the capability request is unavailable. Only the new content
  // search entry is hidden by the feature flag.
  if (enabled !== true) {
    return (
      <div className="masthead-search">
        <IconSearch />
        <input
          className="input"
          aria-label="جست‌وجوی سراسری"
          placeholder="جستجو در سایت…"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              const query = value.trim();
              navigate(`/mdl${query ? `?q=${encodeURIComponent(query)}` : ""}`);
            }
          }}
        />
      </div>
    );
  }

  return (
    <div className="masthead-search" title="جست‌وجوی محتوای مدارک">
      <IconSearch />
      <input
        className="input"
        aria-label="جست‌وجوی محتوای مدارک"
        placeholder="جستجو در مدارک…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && value.trim()) {
            navigate(`/search?q=${encodeURIComponent(value.trim())}`);
          }
        }}
      />
    </div>
  );
}
