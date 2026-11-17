export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export type Severity = "breaking" | "warning" | "additive" | "info";

export interface FindingLocation {
  file: string;
  line: number;
  col: number;
  expression: string;
  function: string;
  access_kind?: string | null;
  field?: string | null;
  path?: string | null;
}

export interface Finding {
  id: string;
  kind: string;
  severity: Severity;
  method?: string | null;
  path?: string | null;
  endpoint?: string | null;
  location: string;
  field_path?: string | null;
  message: string;
  auto_fixable: boolean;
  confidence: string;
  requires_review: boolean;
  affected_count: number;
  old?: unknown;
  new?: unknown;
  locations: FindingLocation[];
}

export interface CodeEdit {
  id: string;
  kind: string;
  file: string;
  line: number;
  col: number;
  old_value?: string | null;
  new_value?: string | null;
  value_expr?: string | null;
  note: string;
}

export interface SkippedEdit {
  change_id: string;
  change_kind: string;
  reason: string;
  location?: { file: string; line: number } | null;
}

export interface Migration {
  id: string;
  scan_id: string;
  status: string;
  edits: CodeEdit[];
  skipped: SkippedEdit[];
  diff: string;
  files: string[];
  stats: {
    edits: number;
    edits_applied: number;
    files_changed: number;
    skipped: number;
    failed: { file: string; edit: string; detail: string }[];
    lines_added: number;
    lines_removed: number;
  };
  verification: Verification | null;
}

export interface TestRun {
  status: string;
  returncode: number | null;
  duration_ms: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  output: string;
}

export interface Verification {
  id: string;
  verdict: "verified" | "baseline_passed" | "patch_failed" | "inconclusive";
  summary: string;
  command: string[];
  baseline: TestRun;
  patched: TestRun;
}

export interface ScanStats {
  diff: {
    total: number;
    by_severity: Record<Severity, number>;
    auto_fixable: number;
    requires_review: number;
  };
  code: {
    files_analyzed: number;
    lines_scanned: number;
    call_sites: number;
    field_accesses: number;
    unmapped_calls: number;
    syntax_errors: { file: string; error: string }[];
    duration_ms: number;
  };
  matched: {
    changes: number;
    breaking: number;
    with_affected_code: number;
    affected_locations: number;
    auto_fixable: number;
    needs_review: number;
  };
  specs: {
    old: { operations: number; fields: number; version: string };
    new: { operations: number; fields: number; version: string };
  };
}

export interface Scan {
  id: string;
  repository_id: string;
  repository_name: string | null;
  status: string;
  stats: ScanStats;
  duration_ms: number;
  created_at: string;
  findings?: Finding[];
  migrations?: { id: string; status: string; stats: Record<string, unknown> }[];
  metadata?: Record<string, unknown>;
}

export interface Repository {
  id: string;
  name: string;
  source_type: string;
  source_ref: string;
  root_path: string;
  file_count: number;
}

export interface Spec {
  id: string;
  name: string;
  version_label: string;
  title: string;
  operation_count: number;
  field_count: number;
}

export interface Benchmark {
  generated_at: string;
  python: string;
  platform: string;
  corpus: { modules: number; lines: number; expected_locations: number };
  detection: {
    precision: number;
    recall: number;
    f1: number;
    true_positives: number;
    false_positives: number;
    false_negatives: number;
    files_per_second: number;
    ms_per_file: number;
    call_site_matches: number;
    changes_detected: number;
    breaking_changes: number;
  };
  scaling: { modules: number; files: number; files_per_second: number; ms_per_file: number }[];
  verification: { status: string; baseline_failed: number; patched_passed: number; summary: string };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail ?? `request failed with ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload as T;
}

export const api = {
  health: () =>
    request<{ status: string; version: string; database: string }>("/api/health"),

  listScans: () => request<Scan[]>("/api/scans"),
  getScan: (id: string) => request<Scan>(`/api/scans/${id}`),
  createScan: (body: {
    repository_id: string;
    old_spec_id: string;
    new_spec_id: string;
    metadata?: Record<string, unknown>;
  }) =>
    request<Scan>("/api/scans", { method: "POST", body: JSON.stringify(body) }),

  listRepositories: () => request<Repository[]>("/api/repositories"),
  listSpecs: () => request<Spec[]>("/api/specs"),
  getSpec: (id: string) => request<Spec & { raw: string }>(`/api/specs/${id}`),

  createMigration: (scanId: string) =>
    request<Migration>(`/api/scans/${scanId}/migration`, { method: "POST" }),
  getMigration: (id: string) => request<Migration>(`/api/migrations/${id}`),
  verifyMigration: (
    id: string,
    body: { test_command?: string; env?: Record<string, string>; timeout_seconds?: number },
  ) =>
    request<Verification>(`/api/migrations/${id}/verify`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  openPullRequest: (id: string, body: { repo_full_name: string; base_branch: string }) =>
    request<{ url: string; number: number | null }>(`/api/migrations/${id}/pull-request`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  diff: (body: { old_spec: string; new_spec: string; metadata?: Record<string, unknown> }) =>
    request<{
      summary: { total: number; by_severity: Record<Severity, number> };
      changes: Finding[];
      specs: { old: { version: string }; new: { version: string } };
    }>("/api/diffs", { method: "POST", body: JSON.stringify(body) }),

  benchmark: () => request<Benchmark>("/api/benchmarks/latest"),
};
