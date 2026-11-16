import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { IconSearch } from "@/components/icons";

/**
 * Additive global content search entry point.
 *
 * Availability of semantic search is reported inside File-QA; the navigation
 * affordance must remain visible so SQLite/basic mode is still discoverable.
 */
export function GlobalSearchBox() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  const openFileQA = () => {
    const query = value.trim();
    navigate(`/file-qa${query ? `?q=${encodeURIComponent(query)}` : ""}`);
  };

  return (
    <form
      className="masthead-search"
      title="جست‌وجوی محتوای مدارک"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        openFileQA();
      }}
    >
      <IconSearch />
      <input
        className="input"
        aria-label="جست‌وجوی محتوای مدارک"
        placeholder="جستجو در مدارک و فایل‌ها…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    </form>
  );
}
