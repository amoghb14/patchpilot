"use client";

import { useState } from "react";
import type { Finding } from "@/lib/api";
import { SeverityBadge } from "@/components/SeverityBadge";
import { humanizeKind } from "@/components/Stat";

export function FindingsTable({ findings }: { findings: Finding[] }) {
  const [selected, setSelected] = useState<string | null>(findings[0]?.id ?? null);
  const current = findings.find((finding) => finding.id === selected) ?? null;

  if (!findings.length) {
    return (
      <div className="px-4 py-10 text-center text-sm text-mist-500">
        No contract changes were detected between these two specs.
      </div>
    );
  }

  return (
    <div className="grid gap-0 lg:grid-cols-[1.35fr_1fr]">
      <div className="max-h-[560px] overflow-auto scroll-thin border-b border-ink-700 lg:border-b-0 lg:border-r">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="sticky top-0 z-10 bg-ink-900 text-[11px] uppercase tracking-[0.12em] text-mist-500">
            <tr>
              <th className="px-4 py-2 font-medium">Severity</th>
              <th className="px-3 py-2 font-medium">Change</th>
              <th className="px-3 py-2 font-medium">Endpoint</th>
              <th className="px-3 py-2 text-right font-medium">Locations</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((finding) => {
              const active = finding.id === selected;
              return (
                <tr
                  key={finding.id}
                  onClick={() => setSelected(finding.id)}
                  className={`cursor-pointer border-t border-ink-800 align-top transition ${
                    active ? "bg-ink-800/70" : "hover:bg-ink-800/40"
                  }`}
                >
                  <td className="px-4 py-2.5">
                    <SeverityBadge severity={finding.severity} />
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="text-mist-100">{humanizeKind(finding.kind)}</p>
                    <p className="mt-0.5 font-mono text-[11px] text-mist-500">
                      {finding.field_path ?? finding.path ?? "—"}
                    </p>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-mist-300">
                    {finding.method ? `${finding.method} ` : ""}
                    {finding.path ?? "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-xs">
                    <span className={finding.affected_count ? "text-mist-100" : "text-mist-500"}>
                      {finding.affected_count}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="max-h-[560px] overflow-auto scroll-thin p-4">
        {current ? (
          <div className="space-y-4">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={current.severity} />
                {current.auto_fixable ? (
                  <span className="chip border-signal-green/40 bg-signal-green/10 text-signal-green">
                    auto-fixable
                  </span>
                ) : (
                  <span className="chip border-signal-amber/40 bg-signal-amber/10 text-signal-amber">
                    needs review
                  </span>
                )}
                <span className="chip border-ink-600 bg-ink-800 text-mist-300">
                  confidence: {current.confidence}
                </span>
              </div>
              <p className="mt-3 text-sm text-mist-100">{current.message}</p>
              <p className="mt-1 font-mono text-[11px] text-mist-500">
                {current.endpoint ?? `${current.method ?? ""} ${current.path ?? ""}`}
              </p>
            </div>

            <div>
              <p className="label">Affected code ({current.locations.length})</p>
              {current.locations.length === 0 ? (
                <p className="rounded border border-dashed border-ink-600 px-3 py-4 text-xs text-mist-500">
                  No code in this repository reads the changed surface. Nothing to migrate.
                </p>
              ) : (
                <ul className="space-y-2">
                  {current.locations.map((location, index) => (
                    <li
                      key={`${location.file}-${location.line}-${index}`}
                      className="rounded border border-ink-700 bg-ink-950/60 px-3 py-2"
                    >
                      <p className="font-mono text-[11px] text-signal-blue">
                        {location.file}:{location.line}:{location.col}
                        <span className="text-mist-500"> · {location.function}</span>
                      </p>
                      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] text-mist-300">
                        {location.expression}
                      </pre>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
