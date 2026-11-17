"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Benchmark, type Repository, type Scan, type Spec } from "@/lib/api";
import { Stat } from "@/components/Stat";
import { DEMO_METADATA } from "@/lib/demo";

export default function DashboardPage() {
  const router = useRouter();
  const [scans, setScans] = useState<Scan[]>([]);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [repositoryId, setRepositoryId] = useState("");
  const [oldSpecId, setOldSpecId] = useState("");
  const [newSpecId, setNewSpecId] = useState("");
  const [metadata, setMetadata] = useState("");

  useEffect(() => {
    Promise.all([api.listScans(), api.listRepositories(), api.listSpecs()])
      .then(([scanList, repoList, specList]) => {
        setScans(scanList);
        setRepositories(repoList);
        setSpecs(specList);
        setRepositoryId(repoList[0]?.id ?? "");
        setOldSpecId(specList[specList.length - 1]?.id ?? "");
        setNewSpecId(specList[0]?.id ?? "");
      })
      .catch((exception: Error) => setError(exception.message))
      .finally(() => setLoading(false));
    api.benchmark().then(setBenchmark).catch(() => setBenchmark(null));
  }, []);

  async function startScan(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!repositoryId || !oldSpecId || !newSpecId) {
      setError("Pick a repository and two specs first.");
      return;
    }
    setBusy(true);
    try {
      let rules: Record<string, unknown> | undefined;
      if (metadata.trim()) {
        rules = JSON.parse(metadata);
      }
      const scan = await api.createScan({
        repository_id: repositoryId,
        old_spec_id: oldSpecId,
        new_spec_id: newSpecId,
        metadata: rules,
      });
      router.push(`/scans/${scan.id}`);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "scan failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <section className="panel relative overflow-hidden px-6 py-8">
        <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-signal-blue/10 blur-3xl" />
        <p className="font-mono text-[11px] uppercase tracking-[0.24em] text-signal-green">
          API migration engine
        </p>
        <h1 className="mt-3 max-w-2xl text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
          Ship the API upgrade <span className="text-mist-300">before</span> it ships you.
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-mist-300">
          PatchPilot compares two OpenAPI contracts, traces the breaking changes into your Python
          source with an AST taint analysis, and writes a source-preserving patch — then proves it by
          running your tests before and after.
        </p>
        <div className="mt-5 flex flex-wrap gap-2 font-mono text-[11px] text-mist-500">
          {["Python AST + LibCST", "OpenAPI 3.x", "SQLAlchemy + FastAPI", "sandboxed pytest"].map(
            (item) => (
              <span key={item} className="chip border-ink-600 bg-ink-800/70 text-mist-300">
                {item}
              </span>
            ),
          )}
        </div>
      </section>

      {benchmark ? (
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Detection precision"
            value={benchmark.detection.precision.toFixed(3)}
            hint={`${benchmark.detection.true_positives} TP · ${benchmark.detection.false_positives} FP`}
            accent="text-signal-green"
          />
          <Stat
            label="Recall"
            value={benchmark.detection.recall.toFixed(3)}
            hint={`${benchmark.detection.false_negatives} missed`}
          />
          <Stat
            label="Analyser throughput"
            value={`${Math.round(benchmark.detection.files_per_second)}/s`}
            hint={`${benchmark.detection.ms_per_file} ms per file`}
          />
          <Stat
            label="Verification"
            value={benchmark.verification.status}
            hint={`${benchmark.verification.baseline_failed} failing → ${benchmark.verification.patched_passed} passing`}
          />
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold">Compatibility scans</h2>
            <span className="font-mono text-[11px] text-mist-500">{scans.length} total</span>
          </div>
          {loading ? (
            <p className="px-4 py-8 text-sm text-mist-500">loading…</p>
          ) : scans.length === 0 ? (
            <div className="px-4 py-8 text-sm text-mist-300">
              <p>No scans yet.</p>
              <p className="mt-2 text-mist-500">
                Seed the demo with <code className="font-mono text-mist-300">python scripts/seed_demo.py</code>{" "}
                or create one on the right.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-ink-800">
              {scans.map((scan) => (
                <li key={scan.id}>
                  <Link
                    href={`/scans/${scan.id}`}
                    className="flex items-center justify-between gap-4 px-4 py-3 transition hover:bg-ink-800/40"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm text-mist-100">
                        {scan.repository_name ?? scan.repository_id.slice(0, 8)}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-mist-500">
                        v{scan.stats?.specs?.old?.version ?? "?"} → v{scan.stats?.specs?.new?.version ?? "?"} ·{" "}
                        {new Date(scan.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 font-mono text-[11px]">
                      <span className="text-signal-red">
                        {scan.stats?.diff?.by_severity?.breaking ?? 0} breaking
                      </span>
                      <span className="text-mist-300">
                        {scan.stats?.matched?.affected_locations ?? 0} locations
                      </span>
                      <span className="text-mist-500">{scan.duration_ms} ms</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold">New scan</h2>
            <span className="font-mono text-[11px] text-mist-500">spec pair</span>
          </div>
          <form onSubmit={startScan} className="space-y-4 px-4 py-4">
            <div>
              <label className="label" htmlFor="repo">
                Repository
              </label>
              <select
                id="repo"
                className="field font-mono text-xs"
                value={repositoryId}
                onChange={(event) => setRepositoryId(event.target.value)}
              >
                {repositories.length === 0 ? <option value="">none imported</option> : null}
                {repositories.map((repository) => (
                  <option key={repository.id} value={repository.id}>
                    {repository.name} · {repository.file_count} py files
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label" htmlFor="old">
                  Old spec
                </label>
                <select
                  id="old"
                  className="field font-mono text-xs"
                  value={oldSpecId}
                  onChange={(event) => setOldSpecId(event.target.value)}
                >
                  {specs.map((spec) => (
                    <option key={spec.id} value={spec.id}>
                      {spec.name} {spec.version_label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="new">
                  New spec
                </label>
                <select
                  id="new"
                  className="field font-mono text-xs"
                  value={newSpecId}
                  onChange={(event) => setNewSpecId(event.target.value)}
                >
                  {specs.map((spec) => (
                    <option key={spec.id} value={spec.id}>
                      {spec.name} {spec.version_label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="label" htmlFor="metadata">
                  Migration metadata (optional)
                </label>
                <button
                  type="button"
                  className="mb-1.5 text-[11px] text-signal-blue hover:underline"
                  onClick={() => setMetadata(DEMO_METADATA)}
                >
                  use demo rules
                </button>
              </div>
              <textarea
                id="metadata"
                rows={6}
                spellCheck={false}
                value={metadata}
                onChange={(event) => setMetadata(event.target.value)}
                placeholder='{"renames": [{"from": "name", "to": "full_name"}]}'
                className="field font-mono text-[11px] leading-relaxed"
              />
              <p className="mt-1 text-[11px] text-mist-500">
                Without a rename rule the engine reports a hypothesis and marks it for review instead of
                rewriting code.
              </p>
            </div>
            {error ? <p className="text-xs text-signal-red">{error}</p> : null}
            <button type="submit" className="btn btn-primary w-full" disabled={busy}>
              {busy ? "analysing…" : "Run compatibility scan"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
