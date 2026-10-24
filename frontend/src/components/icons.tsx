/** Tiny inline SVG icon set — no icon font, no network, RTL-safe. */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const IconDoc = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M6 2h8l5 5v15H6z" />
    <path d="M14 2v5h5M9 12h7M9 16h7M9 8h2" />
  </svg>
);

export const IconDocs = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M8 2h7l4 4v13H8z" />
    <path d="M15 2v4h4" />
    <path d="M5 6v16h11" />
  </svg>
);

export const IconFolder = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M3 7h6l2 2h10v10H3z" />
    <path d="M3 7V5h6l2 2" />
  </svg>
);

export const IconEdit = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M4 20h4l11-11-4-4L4 16z" />
    <path d="M14 5l4 4" />
  </svg>
);

export const IconView = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" />
    <circle cx="12" cy="12" r="2.6" />
  </svg>
);

export const IconPrint = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M7 9V3h10v6" />
    <rect x="4" y="9" width="16" height="7" rx="1" />
    <path d="M7 16h10v5H7z" />
  </svg>
);

export const IconFlow = (p: IconProps) => (
  <svg {...base} {...p}>
    <rect x="3" y="3" width="6" height="5" />
    <rect x="15" y="16" width="6" height="5" />
    <path d="M6 8v6a3 3 0 0 0 3 3h6" />
    <path d="M12 14l3 3-3 3" />
  </svg>
);

export const IconChain = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M9 15l6-6" />
    <path d="M7 12l-2 2a3 3 0 0 0 4 4l2-2" />
    <path d="M17 12l2-2a3 3 0 0 0-4-4l-2 2" />
  </svg>
);

export const IconMail = (p: IconProps) => (
  <svg {...base} {...p}>
    <rect x="2" y="5" width="20" height="14" rx="1" />
    <path d="M2 6l10 7 10-7" />
  </svg>
);

export const IconComment = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M3 4h18v12H8l-5 4z" />
    <path d="M7 9h10M7 12h6" />
  </svg>
);

export const IconAdd = (p: IconProps) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v8M8 12h8" />
  </svg>
);

export const IconDelete = (p: IconProps) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" />
  </svg>
);

export const IconRefresh = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M20 12a8 8 0 1 1-2.6-5.9" />
    <path d="M20 4v4h-4" />
  </svg>
);

export const IconSave = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M4 4h12l4 4v12H4z" />
    <path d="M8 4v6h8V4M8 20v-6h8v6" />
  </svg>
);

export const IconSearch = (p: IconProps) => (
  <svg {...base} {...p}>
    <circle cx="11" cy="11" r="6" />
    <path d="M16 16l5 5" />
  </svg>
);

export const IconArrow = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M20 12H6" />
    <path d="M11 7l-5 5 5 5" />
  </svg>
);

export const IconArchive = (p: IconProps) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="18" height="4" />
    <path d="M5 8v12h14V8" />
    <path d="M10 12h4" />
  </svg>
);

export const IconClock = (p: IconProps) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);

export const IconLock = (p: IconProps) => (
  <svg {...base} {...p}>
    <rect x="5" y="11" width="14" height="9" rx="1" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);

export const IconPaperclip = (p: IconProps) => (
  <svg {...base} {...p}>
    <path d="M20 11l-8 8a4 4 0 0 1-6-6l8-8a3 3 0 0 1 4 4l-8 8a1.6 1.6 0 0 1-2-2l7-7" />
  </svg>
);

export const IconGrid = (p: IconProps) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="18" height="16" />
    <path d="M3 9h18M3 15h18M9 4v16M15 4v16" />
  </svg>
);

export const IconPerson = (p: IconProps) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="8" r="3.4" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </svg>
);
