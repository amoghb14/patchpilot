# PatchPilot

**A tool that notices when a website's API changes, finds the exact lines of your code that will
break, writes the fix, and runs your tests to prove the fix works.**

If that sentence felt like a lot: keep reading. The rest of this page explains every word in it.

---

## The problem, with a normal-person example

Apps rarely work alone. A weather app gets its data from a weather company's **API** — a service on
the internet that answers requests like "what's the temperature in Austin?".

For that to work, both sides have to agree on a **contract**: what questions you're allowed to ask,
and what shape the answer comes back in.

```json
{ "name": "Ada Lovelace", "email": "ada@example.com" }
```

Now the weather company (or Stripe, or Twilio, or anyone) ships a new version. Same idea, but they
decided `name` should be called `full_name`:

```json
{ "full_name": "Ada Lovelace" }
```

Your code still asks for `name`. It gets nothing back, and your app crashes — probably in
production, probably on a Friday.

Finding *every* line that needs updating by hand is slow and error-prone. A big codebase might call
that API in fifty places. Miss one and you ship a bug.

**PatchPilot automates that whole job.** It reads the old contract and the new contract, finds the
differences, tracks down every line of code that depends on the changed parts, writes a small patch,
and then *runs your tests* to show the patch actually works.

---

## Why this is not "just ask ChatGPT to rewrite it"

The hard part is not editing text. The hard part is **being sure**.

Three things make this project interesting:

1. **The tool has to know which `name` is which.** Your program might have a local dictionary called
   `customer` that also has a `name` key — one that has nothing to do with the API. A dumb
   find-and-replace would break it. PatchPilot follows the data from the actual HTTP request, so it
   only touches values that really came from that API.
2. **A rename must be authorised.** Guessing that a removed field was renamed to a similar-looking
   new field is how migration bots destroy code. Unless the API provider publishes an explicit rule
   ("`name` became `full_name`"), PatchPilot reports a *suspicion* and asks a human.
3. **The patch must be proven.** A patch is only called `verified` when the unpatched code fails the
   tests under the new contract **and** the patched code passes them.

---

## Does it actually work?

Yes, and you can check it yourself in about a minute. The repo contains a small demo app in
`examples/acme_client` built for version 1.4.0 of a fake "Acme" API. A second contract (2.0.0) in
`fixtures/acme_v2.yaml` breaks it on purpose: fields get renamed, one is deleted, one endpoint moves,
and the update call now requires a new field.

Running the real pipeline gives:

| Step | Result |
| --- | --- |
| Compare the two contracts | 30 changes, 21 of them breaking |
| Scan the demo code | 13 files, 5 API call sites, 17 code locations affected |
| Generate the patch | 7 edits across 2 files |
| Run the tests before the patch | **5 failed** |
| Run the tests after the patch | **9 passed** → verdict: `verified` |

The tool also leaves `app/legacy.py` completely alone. That file has `customer["name"]` three times —
but those dictionaries come from a CSV import, not from the API. Not touching them is the whole point.

There is a benchmark too (run `scripts/benchmark.py` to reproduce). On a generated test set with 400
known answers, it found all 400, with **zero false positives** — 100% precision and 100% recall — at
roughly **800 files per second**. Speed varies with whatever else your laptop is doing: repeated runs
landed between 700 and 1,060 files/s. That's a synthetic test set, so it's a check that the code
works, not a promise about every real-world repo.

---

## Try it on your computer

You need two things installed:

| Tool | Version | Check it with |
| --- | --- | --- |
| Python | 3.11 or newer | `python3 --version` |
| Node.js | 20 or newer | `node --version` |

If either command says "not found", install it from [python.org](https://www.python.org/downloads/)
and [nodejs.org](https://nodejs.org), then reopen your terminal.

Everything below uses two terminal windows. **Window 1** runs the backend (the analysis engine).
**Window 2** runs the dashboard (what you see in a browser).

### Step 1 — install the backend

```bash
cd ~/Downloads/patchpilot
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

A virtual environment (`.venv`) is a private folder of libraries so this project can't mess up
anything else on your computer. Activating it isn't needed — every command starts with
`.venv/bin/`, which uses it directly.

### Step 2 — load the demo

```bash
.venv/bin/python scripts/seed_demo.py
```

You should see something like:

```
repository   acme-client (13 python files)
scan         c5c7348a-...
  changes    30 total, 21 breaking
  affected   17 code locations
migration    7ff85940-... (7 edits, 2 files)
verification verified: Regression reproduced (5 failing, 0 errors) and the patch passes 9 tests
```

That last line is the whole project in one sentence: it broke the app, fixed it, and proved it.

### Step 3 — start the API (Window 1)

```bash
cd ~/Downloads/patchpilot/backend
../.venv/bin/uvicorn patchpilot.main:app --port 8000
```

Leave it running. Check it worked by opening <http://127.0.0.1:8000/docs> — you'll get a clickable
list of every API endpoint.

### Step 4 — start the dashboard (Window 2)

```bash
cd ~/Downloads/patchpilot/frontend
npm install
npm run dev
```

Open <http://localhost:3000>. You should see the demo scan, a red "21 breaking" number, a list of
changes with the code they touch, a coloured diff, and a green `verified` result.

### Step 5 — try the buttons

- Click a row in **Compatibility report** to see the exact file, line, and code snippet.
- In **Migration**, the diff shows the patch (nothing is written to your files — it lives in a
  throwaway copy).
- Press **Verify patch**. It runs the demo's test suite twice — once without the patch (fails) and
  once with it (passes) — and prints both results.
- **Diff playground** lets you paste any two OpenAPI files and compare them.

### Windows instead of macOS/Linux

Replace `.venv/bin/...` with `.venv\Scripts\...`:

```bat
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
.venv\Scripts\python scripts\seed_demo.py
```

---

## How it works, in four steps

Think of it as four people working together.

**1. The map reader.** Two OpenAPI files are maps of the same territory, drawn a year apart.
PatchPilot lines them up and lists what changed. The tricky part is renames: if a list changed its
key from `data` to `results`, and each item inside changed `name` to `full_name`, that's two linked
changes — not four random ones. The tool tracks them as a chain.

**2. The detective.** For every API call in your Python code, it works out *which endpoint* is being
called, then follows the returned data around that function: through `.json()`, into variables,
through loops and list comprehensions. Only reads that can be traced back to that endpoint count as
affected. This is called **taint analysis** — data from the API is "tainted", and it stays tainted as
it flows.

**3. The careful editor.** It rewrites only what it can justify, and only the exact characters that
need to change (that's what **LibCST** does). Comments stay, spacing stays, the untouched 99% of the
file stays byte-for-byte identical. If a change isn't safe to automate, it goes into a "needs a
human" list with a reason.

**4. The experiment.** Changing code isn't proof. It copies your repo into a temporary folder, runs
your tests against the *new* contract without the patch (they must fail — otherwise the test suite
can't prove anything), then runs them again with the patch applied (they must pass). Only then is the
migration labelled `verified`.

Want the full version — algorithms, data structures, design trade-offs, API reference? See
**[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)**.

---

## What's in the box

```
backend/            the engine and the API (Python, FastAPI, SQLAlchemy)
  engine/openapi/     reading and comparing API contracts
  engine/analysis/    finding the code that depends on them
  engine/migration/   writing the patch
  engine/verification/running tests before and after
frontend/           the dashboard (Next.js, TypeScript, Tailwind, Monaco)
examples/acme_client/a small app that really breaks under the new contract
fixtures/           the two Acme contracts + the rename rules
scripts/            seed_demo.py (load the demo), benchmark.py (measure accuracy/speed)
benchmarks/         the last measured numbers
docs/               the technical deep dive
```

Tests and measurements:

```bash
cd backend && ../.venv/bin/python -m pytest      # 32 tests
.venv/bin/python scripts/benchmark.py            # writes benchmarks/latest.json
cd frontend && npm run build                     # production build
```

---

## Words you'll meet

| Word | What it means here |
| --- | --- |
| **API** | A service another program talks to over the internet. |
| **OpenAPI / spec** | A file that describes an API: its URLs, inputs and outputs. The "contract". |
| **Breaking change** | A change that makes existing code fail — a renamed or deleted field, a new required input, a moved URL. |
| **Endpoint** | One specific operation, like `GET /v1/customers/{id}`. |
| **AST** | "Abstract syntax tree" — your code turned into a tree of parts, so a program can reason about it instead of just matching text. |
| **Taint analysis** | Tracking where a value came from as it moves through code. |
| **LibCST** | A library that edits Python code while keeping its exact formatting. |
| **Sandbox** | A throwaway copy of your project, used so experiments can't damage the original. |
| **Verified** | The unpatched code failed the tests and the patched code passed them. Anything less isn't called verified. |
| **Precision / recall** | Of everything the tool flagged, how much was right (precision); of everything it *should* have found, how much it found (recall). |

---

## When something goes wrong

| Symptom | Fix |
| --- | --- |
| `python3: command not found` | Install Python 3.11+ and reopen the terminal. On Windows try `python`. |
| `SyntaxError` while installing | You're on Python 3.9 or older. Check with `python3 --version`. |
| `Address already in use` (port 8000) | Something else is on that port: run uvicorn with `--port 8001`, and start the dashboard with `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npm run dev`. |
| Dashboard says "failed to load scan" | The API isn't running. Redo Step 3 and reload. |
| `Verify` says `baseline_passed` | The demo was pointed at the *old* contract. The environment field should be `ACME_API_MODE=v2`. |
| `Create PR` does nothing | It needs a GitHub token on the server (`PATCHPILOT_GITHUB_TOKEN`). The rest works without it. |
| Tests in `examples/` fail | Expected if you run them with `ACME_API_MODE=v2` before patching — that's the breakage being demonstrated. |

---

## Honest limits

- **Python only.** The analyser reads Python. JavaScript/TypeScript would be a second analyser.
- **Not every change can be fixed automatically.** Type changes, removed enum values and deleted
  endpoints are reported and sent to a human, on purpose.
- **The verifier is process isolation, not a security vault.** It uses a temp folder, a timeout and a
  clean environment. For untrusted code you'd run it inside a container.
- **Macro-level judgement is still yours.** The tool tells you what the patch does and proves the
  tests pass; deciding whether the new behaviour is *desirable* is a human call.

---

## If you're reading this because you want to build something similar

Two lessons this project taught me, in case they help:

1. **The interesting work is in the boring parts.** Parsing contracts and tracking variables is 80%
   of the code, and it's what makes the "AI fixes your code" demo trustworthy.
2. **Refusing to act is a feature.** The feature I'm most pleased with is the list of changes the tool
   *won't* touch. Being wrong quietly is worse than being slow.

Built as a portfolio project. Licensed under MIT — see [LICENSE](LICENSE).
