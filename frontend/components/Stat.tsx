export function Stat({
  label,
  value,
  hint,
  accent = "text-mist-100",
}: {
  label: string;
  value: string | number;
  hint?: string;
  accent?: string;
}) {
  return (
    <div className="panel px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-mist-500">{label}</p>
      <p className={`mt-1 font-mono text-2xl leading-tight ${accent}`}>{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-mist-500">{hint}</p> : null}
    </div>
  );
}

export function humanizeKind(kind: string): string {
  return kind.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
