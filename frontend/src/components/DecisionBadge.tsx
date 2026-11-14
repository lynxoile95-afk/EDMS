import { Badge } from "./ui";
import { DECISION_STYLES, confidenceClass, percent } from "@/lib/format";

export function DecisionBadge({ decision }: { decision: string }) {
  const style = DECISION_STYLES[decision] ?? DECISION_STYLES.PENDING;
  return <Badge className={style.className}>{style.label}</Badge>;
}

export function ConfidenceBadge({
  value,
  label,
  source,
}: {
  value: number;
  label?: string;
  source?: string;
}) {
  return (
    <span
      className={`badge ${confidenceClass(value)}`}
      title={source ? `روش استخراج: ${source}` : undefined}
    >
      {label ? `${label}: ` : ""}
      {percent(value)}
    </span>
  );
}
