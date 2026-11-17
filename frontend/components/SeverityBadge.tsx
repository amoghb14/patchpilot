import type { Severity } from "@/lib/api";

const styles: Record<Severity, string> = {
  breaking: "border-signal-red/40 bg-signal-red/10 text-signal-red",
  warning: "border-signal-amber/40 bg-signal-amber/10 text-signal-amber",
  additive: "border-signal-blue/40 bg-signal-blue/10 text-signal-blue",
  info: "border-ink-600 bg-ink-800 text-mist-300",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`chip ${styles[severity] ?? styles.info}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {severity}
    </span>
  );
}

export function VerdictBadge({ verdict }: { verdict: string }) {
  const palette =
    verdict === "verified"
      ? "border-signal-green/40 bg-signal-green/10 text-signal-green"
      : verdict === "baseline_passed"
        ? "border-signal-amber/40 bg-signal-amber/10 text-signal-amber"
        : "border-signal-red/40 bg-signal-red/10 text-signal-red";
  return <span className={`chip ${palette}`}>{verdict.replace(/_/g, " ")}</span>;
}
