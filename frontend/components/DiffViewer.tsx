"use client";

import dynamic from "next/dynamic";

const MonacoDiffEditor = dynamic(
  () => import("@monaco-editor/react").then((module) => module.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-full place-items-center font-mono text-xs text-mist-500">loading editor…</div>
    ),
  },
);

/** Rebuild the two sides of a unified diff so Monaco can render it side by side. */
export function splitUnifiedDiff(diff: string): { original: string; modified: string } {
  const left: string[] = [];
  const right: string[] = [];
  for (const line of diff.split("\n")) {
    if (
      line.startsWith("--- ") ||
      line.startsWith("+++ ") ||
      line.startsWith("@@") ||
      line.startsWith("diff --git")
    ) {
      continue;
    }
    if (line.startsWith("-")) {
      left.push(line.slice(1));
      continue;
    }
    if (line.startsWith("+")) {
      right.push(line.slice(1));
      continue;
    }
    const text = line.startsWith(" ") ? line.slice(1) : line;
    left.push(text);
    right.push(text);
  }
  return { original: left.join("\n"), modified: right.join("\n") };
}

export function DiffViewer({ diff, height = 420 }: { diff: string; height?: number }) {
  if (!diff.trim()) {
    return (
      <div className="grid h-40 place-items-center rounded-md border border-ink-700 bg-ink-950/60 font-mono text-xs text-mist-500">
        no changes
      </div>
    );
  }
  const { original, modified } = splitUnifiedDiff(diff);
  return (
    <div className="overflow-hidden rounded-md border border-ink-700" style={{ height }}>
      <MonacoDiffEditor
        original={original}
        modified={modified}
        language="python"
        theme="vs-dark"
        options={{
          readOnly: true,
          renderSideBySide: true,
          minimap: { enabled: false },
          fontSize: 12,
          scrollBeyondLastLine: false,
          renderOverviewRuler: false,
          padding: { top: 10, bottom: 10 },
        }}
      />
    </div>
  );
}

export const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((module) => module.Editor),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-full place-items-center font-mono text-xs text-mist-500">loading editor…</div>
    ),
  },
);
