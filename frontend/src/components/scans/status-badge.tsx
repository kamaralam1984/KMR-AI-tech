import { Badge, type Tone } from "@/components/ui/badge";
import { STATUS_LABEL } from "@/lib/scan-text";
import type { ScanStatus } from "@/lib/types";

const TONE: Record<ScanStatus, Tone> = {
  queued: "neutral",
  running: "info",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

export function StatusBadge({ status }: { status: ScanStatus }) {
  return <Badge tone={TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}
