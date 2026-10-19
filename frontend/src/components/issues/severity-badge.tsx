import { Badge, type Tone } from "@/components/ui/badge";
import { SEVERITY_LABEL } from "@/lib/issue-text";
import type { Severity } from "@/lib/types";

const TONE: Record<Severity, Tone> = {
  critical: "danger",
  high: "danger",
  medium: "warning",
  low: "neutral",
  info: "info",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={TONE[severity]}>{SEVERITY_LABEL[severity]}</Badge>;
}
