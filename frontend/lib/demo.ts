/** Sample inputs for the Acme demo, mirrored from fixtures/migration_metadata.json. */
export const DEMO_METADATA = `{
  "renames": [
    { "location": "response", "from": "name", "to": "full_name" },
    { "location": "response", "from": "data", "to": "results",
      "endpoints": ["GET /v1/customers"] }
  ],
  "path_changes": [
    { "from": "GET /v1/invoices/{invoice_id}", "to": "GET /v2/invoices/{invoice_id}" }
  ],
  "required_fields": [
    { "endpoint": "PATCH /v1/customers/{customer_id}",
      "location": "request_body", "field": "change_reason",
      "value_expr": "\\"patchpilot migration\\"" }
  ],
  "removals": [
    { "endpoint": "GET /v1/customers/{customer_id}", "field": "email",
      "strategy": "optional_access" }
  ]
}`;

export const DEMO_TEST_COMMAND = "python -m pytest -q";
export const DEMO_ENV = "ACME_API_MODE=v2";

export function parseEnv(text: string): Record<string, string> {
  const entries: [string, string][] = [];
  for (const chunk of text.split(/[\n,]/)) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;
    const index = trimmed.indexOf("=");
    if (index <= 0) continue;
    entries.push([trimmed.slice(0, index).trim(), trimmed.slice(index + 1).trim()]);
  }
  return Object.fromEntries(entries);
}
