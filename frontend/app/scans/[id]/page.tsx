"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  type Migration,
  type Scan,
  type Verification,
} from "@/lib/api";
import { DiffViewer } from "@/components/DiffViewer";
import { FindingsTable } from "@/components/FindingsTable";
import { SeverityBadge, VerdictBadge } from "@/components/SeverityBadge";
import { Stat, humanizeKind } from "@/components/Stat";
import { DEMO_ENV, DEMO_TEST_COMMAND, parseEnv } from "@/lib/demo";

export default function ScanDetailPage() {
  const params = useParams<{ id: string }>();
  const scanId = params?.id ?? "";

  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [migration, setMigration] = useState<Migration | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [testCommand, setTestCommand] = useState(DEMO_TEST_COMMAND);
  const [envText, setEnvText] = useState(DEMO_ENV);
  const [prRepo, setPrRepo] = useState("");
  const [prNote, setPrNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.getScan(scanId);
      setScan(result);
      if (result.migrations?.length) {
        const latest = await api.getMigration(result.migrations[0].id);
        setMigration(latest);
        setVerification(latest.verification ?? null);
      }
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "failed to load scan");
    }
  }, [scanId]);

  useEffect(() => {
    if (scanId) void load();
  }, [scanId, load]);

  async function generateMigration() {
    setBusy("migration");
    setError(null);
    try {
      const result = await api.createMigration(scanId);
      setMigration(result);
      setVerification(result.verification ?? null);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "migration failed");
    } finally {
      setBusy(null);
    }
  }

  async function verify() {
    if (!migration) return;
    setBusy("verify");
    setError(null);
    try {
      const result = await api.verifyMigration(migration.id, {
        test_command: testCommand,
        env: parseEnv(envText),
        timeout_seconds: 180,
      });
      setVerification(result);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "verification failed");
    } finally {
      setBusy(null);
    }
  }

  async function openPullRequest() {
    if (!migration) return;
    setBusy("pr");
    setPrNote(null);
    try {
      const result = await api.openPullRequest(migration.id, {
        repo_full_name: prRepo,
        base_branch: "main",
      });
      setPrNote(result.url ? `pull request #${result.number}: ${result.url}` : "pull request created");
    } catch (exception) {
      setPrNote(exception instanceof Error ? exception.message : "pull request failed");
    } finally {
      setBusy(null);
    }
  }

  if (error && !scan) {
    return (
      <div className="panel px-5 py-6 text-sm text-signal-red">
        {error}
        <p className="mt-2 text-mist-500">
          Is the API running on port 8000? <Link className="underline" href="/">Back to dashboard</Link>
        </p>
      </div>
    );
  }

  if (!scan) {
    return <p className="text-sm text-mist-500">loading scan…</p>;
  }

  const stats = scan.stats;
  const findings = scan.findings ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href="/" className="font-mono text-[11px] text-mist-500 hover:text-mist-100">
            ← all scans
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            {scan.repository_name ?? "repository"}
          </h1>
          <p className="mt-1 font-mono text-[11px] text-mist-500">
            {stats.specs.old.operations} operations · v{stats.specs.old.version} →{" "}
            {stats.specs.new.operations} operations · v{stats.specs.new.version} ·{" "}
            {new Date(scan.created_at).toLocaleString()}
          </p>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px] text-mist-500">
          <span className="chip border-ink-600 bg-ink-800 text-mist-300">
            {stats.code.files_analyzed} files analysed
          </span>
          <span className="chip border-ink-600 bg-ink-800 text-mist-300">
            {stats.code.field_accesses} field reads
          </span>
          <span className="chip border-ink-600 bg-ink-800 text-mist-300">
            {scan.duration_ms} ms
          </span>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Breaking changes"
          value={stats.diff.by_severity.breaking}
          hint={`${stats.diff.total} changes total`}
          accent="text-signal-red"
        />
        <Stat
          label="Affected locations"
          value={stats.matched.affected_locations}
          hint={`${stats.matched.with_affected_code} changes touch code`}
        />
        <Stat
          label="Auto-fixable"
          value={stats.matched.auto_fixable}
          hint={`${stats.matched.needs_review} need a human`}
          accent="text-signal-green"
        />
        <Stat
          label="Call sites"
          value={stats.code.call_sites}
          hint={`${stats.code.unmapped_calls} unmapped`}
        />
      </div>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <h2 className="text-sm font-semibold">Compatibility report</h2>
          <div className="flex gap-2">
            <SeverityBadge severity="breaking" />
            <span className="chip border-ink-600 bg-ink-800 text-mist-300">{findings.length} findings</span>
          </div>
        </div>
        <FindingsTable findings={findings as never} />
      </section>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <div>
            <h2 className="text-sm font-semibold">Migration</h2>
            <p className="mt-0.5 text-[11px] text-mist-500">
              Only changes with explicit rules and a proven code location become edits.
            </p>
          </div>
          {!migration ? (
            <button className="btn btn-primary" onClick={generateMigration} disabled={busy !== null}>
              {busy === "migration" ? "generating…" : "Generate migration"}
            </button>
          ) : (
            <span className="chip border-ink-600 bg-ink-800 text-mist-300">
              {migration.stats.edits} edits · {migration.stats.files_changed} files
            </span>
          )}
        </div>

        {migration ? (
          <div className="space-y-4 px-4 py-4">
            <div className="grid gap-2 sm:grid-cols-4">
              <Stat label="Edits" value={migration.stats.edits} />
              <Stat label="Applied" value={migration.stats.edits_applied} accent="text-signal-green" />
              <Stat label="Skipped" value={migration.stats.skipped} accent="text-signal-amber" />
              <Stat
                label="Diff"
                value={`+${migration.stats.lines_added}/-${migration.stats.lines_removed}`}
                hint={migration.files.join(", ") || "no files"}
              />
            </div>

            {migration.stats.failed.length > 0 ? (
              <ul className="space-y-1 rounded border border-signal-red/40 bg-signal-red/5 px-3 py-2 font-mono text-[11px] text-signal-red">
                {migration.stats.failed.map((failure, index) => (
                  <li key={index}>
                    {failure.file}: {failure.detail}
                  </li>
                ))}
              </ul>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <DiffViewer diff={migration.diff} height={430} />

              <div className="space-y-3">
                <div>
                  <label className="label" htmlFor="cmd">
                    Test command
                  </label>
                  <input
                    id="cmd"
                    className="field font-mono text-[11px]"
                    value={testCommand}
                    onChange={(event) => setTestCommand(event.target.value)}
                  />
                </div>
                <div>
                  <label className="label" htmlFor="env">
                    Environment (KEY=value)
                  </label>
                  <input
                    id="env"
                    className="field font-mono text-[11px]"
                    value={envText}
                    onChange={(event) => setEnvText(event.target.value)}
                    placeholder="ACME_API_MODE=v2"
                  />
                  <p className="mt-1 text-[11px] text-mist-500">
                    Point the suite at the new contract so the regression is reproduced.
                  </p>
                </div>
                <button className="btn w-full" onClick={verify} disabled={busy !== null}>
                  {busy === "verify" ? "running suite twice…" : "Verify patch"}
                </button>
                {error ? <p className="text-[11px] text-signal-red">{error}</p> : null}

                <div className="rounded border border-ink-700 bg-ink-950/50 p-3">
                  <p className="label">Open a pull request</p>
                  <input
                    className="field mb-2 font-mono text-[11px]"
                    value={prRepo}
                    onChange={(event) => setPrRepo(event.target.value)}
                    placeholder="owner/repo"
                  />
                  <button
                    className="btn w-full"
                    onClick={openPullRequest}
                    disabled={busy !== null || verification?.verdict !== "verified" || !prRepo}
                  >
                    {busy === "pr" ? "opening…" : "Create PR"}
                  </button>
                  <p className="mt-2 text-[11px] text-mist-500">
                    {verification?.verdict === "verified"
                      ? "Server needs PATCHPILOT_GITHUB_TOKEN to write to GitHub."
                      : "Verification must pass before a PR can be opened."}
                  </p>
                  {prNote ? <p className="mt-1 break-words text-[11px] text-signal-blue">{prNote}</p> : null}
                </div>
              </div>
            </div>

            <div className="grid gap-2 md:grid-cols-2">
              <EditsList edits={migration.edits} />
              <SkippedList skipped={migration.skipped} />
            </div>
          </div>
        ) : (
          <p className="px-4 py-8 text-sm text-mist-500">
            Generate a migration to see the patch. Nothing is written to your repository — the patch is
            materialised in a disposable sandbox for verification.
          </p>
        )}
      </section>

      {verification ? (
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">Verification</h2>
              <VerdictBadge verdict={verification.verdict} />
            </div>
            <span className="font-mono text-[11px] text-mist-500">
              {verification.command.join(" ")}
            </span>
          </div>
          <div className="space-y-4 px-4 py-4">
            <p className="text-sm text-mist-300">{verification.summary}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <RunCard title="Baseline (unpatched, new contract)" run={verification.baseline} />
              <RunCard title="Patched" run={verification.patched} />
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function EditsList({ edits }: { edits: Migration["edits"] }) {
  return (
    <div className="rounded border border-ink-700">
      <p className="border-b border-ink-700 px-3 py-2 text-[11px] uppercase tracking-[0.14em] text-mist-500">
        Planned edits
      </p>
      <ul className="max-h-52 divide-y divide-ink-800 overflow-auto scroll-thin">
        {edits.map((edit) => (
          <li key={edit.id} className="px-3 py-2 font-mono text-[11px]">
            <span className="text-signal-green">{edit.kind}</span>{" "}
            <span className="text-mist-300">
              {edit.file}:{edit.line}
            </span>
            <p className="mt-0.5 text-mist-500">{edit.note}</p>
          </li>
        ))}
        {edits.length === 0 ? <li className="px-3 py-3 text-[11px] text-mist-500">no edits</li> : null}
      </ul>
    </div>
  );
}

function SkippedList({ skipped }: { skipped: Migration["skipped"] }) {
  return (
    <div className="rounded border border-ink-700">
      <p className="border-b border-ink-700 px-3 py-2 text-[11px] uppercase tracking-[0.14em] text-mist-500">
        Sent to review ({skipped.length})
      </p>
      <ul className="max-h-52 divide-y divide-ink-800 overflow-auto scroll-thin">
        {skipped.slice(0, 12).map((item, index) => (
          <li key={index} className="px-3 py-2 font-mono text-[11px]">
            <span className="text-signal-amber">{humanizeKind(item.change_kind)}</span>
            {item.location ? (
              <span className="text-mist-500">
                {" "}
                · {item.location.file}:{item.location.line}
              </span>
            ) : null}
            <p className="mt-0.5 text-mist-500">{item.reason}</p>
          </li>
        ))}
        {skipped.length === 0 ? (
          <li className="px-3 py-3 text-[11px] text-mist-500">nothing skipped</li>
        ) : null}
      </ul>
    </div>
  );
}

function RunCard({ title, run }: { title: string; run: Verification["baseline"] }) {
  const tone =
    run.status === "passed"
      ? "border-signal-green/40 bg-signal-green/5"
      : run.status === "failed"
        ? "border-signal-red/40 bg-signal-red/5"
        : "border-signal-amber/40 bg-signal-amber/5";
  return (
    <div className={`rounded border ${tone} px-3 py-3`}>
      <div className="flex items-center justify-between">
        <p className="text-xs text-mist-300">{title}</p>
        <span className="chip border-ink-600 bg-ink-900 font-mono text-[11px]">{run.status}</span>
      </div>
      <div className="mt-2 flex gap-4 font-mono text-[11px] text-mist-300">
        <span className="text-signal-green">{run.passed} passed</span>
        <span className="text-signal-red">{run.failed} failed</span>
        <span>{run.errors} errors</span>
        <span className="text-mist-500">{run.duration_ms} ms</span>
      </div>
      <details className="mt-2">
        <summary className="cursor-pointer font-mono text-[11px] text-mist-500">output</summary>
        <pre className="mt-2 max-h-48 overflow-auto scroll-thin whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-mist-300">
          {run.output}
        </pre>
      </details>
    </div>
  );
}
