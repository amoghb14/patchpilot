"use client";

import { useState } from "react";
import { api, type Finding, type Spec } from "@/lib/api";
import { MonacoEditor } from "@/components/DiffViewer";
import { FindingsTable } from "@/components/FindingsTable";
import { SeverityBadge } from "@/components/SeverityBadge";
import { DEMO_METADATA } from "@/lib/demo";

export default function DiffPlaygroundPage() {
  const [oldSpec, setOldSpec] = useState("");
  const [newSpec, setNewSpec] = useState("");
  const [metadata, setMetadata] = useState(DEMO_METADATA);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [summary, setSummary] = useState<{ total: number; by_severity: Record<string, number> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [specs, setSpecs] = useState<Spec[]>([]);

  async function loadDemoSpecs() {
    try {
      const list = await api.listSpecs();
      setSpecs(list);
      const oldOne = list.find((spec) => spec.version_label.startsWith("1"));
      const newOne = list.find((spec) => spec.version_label.startsWith("2"));
      if (!oldOne || !newOne) {
        setError("seed the demo first: python scripts/seed_demo.py");
        return;
      }
      const [oldRaw, newRaw] = await Promise.all([api.getSpec(oldOne.id), api.getSpec(newOne.id)]);
      setOldSpec(oldRaw.raw);
      setNewSpec(newRaw.raw);
      setError(null);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "could not load specs");
    }
  }

  async function compare() {
    setBusy(true);
    setError(null);
    try {
      const body: { old_spec: string; new_spec: string; metadata?: Record<string, unknown> } = {
        old_spec: oldSpec,
        new_spec: newSpec,
      };
      if (metadata.trim()) body.metadata = JSON.parse(metadata);
      const result = await api.diff(body);
      setSummary(result.summary);
      setFindings(
        result.changes.map((change) => ({
          ...change,
          affected_count: 0,
          locations: [],
        })),
      );
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "diff failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Diff playground</h1>
          <p className="mt-1 max-w-2xl text-sm text-mist-300">
            Compare any two OpenAPI 3.x documents. Rules in the metadata pane authorise renames; without
            them the engine reports a hypothesis and flags it for review.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={loadDemoSpecs}>
            Load demo specs
          </button>
          <button className="btn btn-primary" onClick={compare} disabled={busy || !oldSpec || !newSpec}>
            {busy ? "comparing…" : "Compare"}
          </button>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <SpecPane title="Old contract" value={oldSpec} onChange={setOldSpec} />
        <SpecPane title="New contract" value={newSpec} onChange={setNewSpec} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2 className="text-sm font-semibold">Migration metadata</h2>
          <span className="font-mono text-[11px] text-mist-500">optional</span>
        </div>
        <textarea
          rows={6}
          spellCheck={false}
          value={metadata}
          onChange={(event) => setMetadata(event.target.value)}
          className="w-full resize-y bg-transparent px-4 py-3 font-mono text-[11px] leading-relaxed text-mist-100 outline-none"
        />
      </div>

      {error ? (
        <p className="rounded border border-signal-red/40 bg-signal-red/5 px-3 py-2 text-xs text-signal-red">
          {error}
        </p>
      ) : null}

      {findings ? (
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <h2 className="text-sm font-semibold">Contract changes</h2>
            <div className="flex items-center gap-2">
              <SeverityBadge severity="breaking" />
              <span className="chip border-ink-600 bg-ink-800 text-mist-300">
                {summary?.by_severity?.breaking ?? 0} breaking / {summary?.total ?? 0} total
              </span>
            </div>
          </div>
          <FindingsTable findings={findings} />
        </section>
      ) : null}
    </div>
  );
}

function SpecPane({
  title,
  value,
  onChange,
}: {
  title: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="panel overflow-hidden">
      <div className="panel-header">
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className="font-mono text-[11px] text-mist-500">{value.split("\n").length} lines</span>
      </div>
      <div className="h-[340px]">
        <MonacoEditor
          value={value}
          onChange={(next) => onChange(next ?? "")}
          language="yaml"
          theme="vs-dark"
          options={{
            minimap: { enabled: false },
            fontSize: 12,
            scrollBeyondLastLine: false,
            padding: { top: 10 },
            tabSize: 2,
          }}
        />
      </div>
    </div>
  );
}
