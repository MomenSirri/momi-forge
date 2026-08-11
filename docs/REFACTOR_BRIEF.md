# Refactor brief: Momi Forge structural cleanup

You are working in `D:\Momi Forge`, a Gradio + FastAPI app that submits ComfyUI
workflows to RunPod serverless endpoints. Windows, PowerShell, Python 3.12.
The virtualenv interpreter is `.venv\Scripts\python.exe` — use it for everything.

Four tasks, described below. **Do them one at a time, in the order given, and
commit each one separately.** Do not start the next task until the current one
passes the verification gate. Do not batch the commits.

Everything here is a refactor: no user-visible behavior may change. If you find
a bug while moving code, do not fix it inside the move — note it, finish the
move, then fix it in a separate commit so the diff stays reviewable.

---

## Ground rules

### Commands

```powershell
# tests (83 must pass before and after every task)
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .

# the app must import and build its FastAPI server
.\.venv\Scripts\python.exe -c "import app; app._create_server_app(); print('OK')"
```

`import app` takes ~9s and builds the whole Gradio UI, so it catches most
breakage. It is not optional — run it after every task.

### Invariants you must not break

1. **Test count only goes up.** 83 tests pass today. Never delete a test to make
   a refactor pass. If a test genuinely encodes old structure, adapt it and say
   so in the commit message.
2. **Gradio `gr.Request` injection.** Handlers annotated `request: gr.Request`
   receive the request by annotation. Every module uses
   `from __future__ import annotations`, so annotations are strings resolved
   against the *defining module's* globals — a moved handler must keep `gradio`
   imported as `gr` in its new home. Verify with:
   ```python
   from gradio.helpers import special_args
   args, _, _ = special_args(fn, inputs=[], request="SENTINEL")
   assert args == ["SENTINEL"]
   ```
3. **Workflow debug dump filenames.** `workflow_ui.save_workflow_debug_json`
   produces `<prefix>_<sanitized workflow>_<task_id>_<YYYYmmdd_HHMMSS>.json`.
   Prefixes are `general`, `upscaler`, `reference_generator`, `flux2_klein`.
   General Enhancement and Pro Upscaler fall back to their module `WORKFLOW_NAME`
   when the name is blank; the other two fall back to the literal `workflow`.
   These are covered by `tests/test_workflow_ui.py`.
4. **Portal token signing is frozen.** The history portal URL signature is
   `HMAC-SHA256(secret, f"{email}\n{exp}\n{nonce}")` and a Node service in
   `history_portal/server.js` validates it independently. Do not change the
   scheme, the field order, or the context prefixes in `portal_auth.py`.
5. **`HISTORY_PORTAL_SSO_SECRET` stays required.** `app.py`,
   `history_portal/server.js`, `start_momi_forge.bat`, and
   `scripts\Start-MomiForgeComponent.ps1` all refuse to start without it and
   reject the retired placeholder `momi-forge-local-sso-secret` by value. Keep
   all four in agreement.
6. **Env var names are a public contract.** The launchers and `.env` set them.
   Do not rename any `os.getenv(...)` key.
7. **Node IDs are data, not magic numbers.** Constants like `NODE_IMAGE_1 = "76"`
   map to ComfyUI graph nodes in the workflow JSON under `api_workflow/`. Never
   renumber or "tidy" them.

### Style

Match the surrounding code: `from __future__ import annotations`, full type
hints, module-level constants in SCREAMING_SNAKE, private helpers prefixed `_`,
comments only where the *why* is non-obvious. No new dependencies.

---

## Task 1 — Consolidate the three progress trackers

**Why:** three hand-rolled state machines translate the same RunPod/ComfyUI
progress text into the same UI percentages, with three sets of bugs. This is the
largest remaining duplication in the repo (~1,200 lines).

**The three implementations:**

| Location | Entry point | Shape |
|---|---|---|
| `General_Enhancement_v04.py` lines 451–1430 | `_update_progress_tracker_from_text` (line 1150) | richest: multi-stage, tile estimation, runtime totals, cycle reconciliation |
| `reference_generator.py` lines 352–608 | `_update_progress_tracker_from_text` (line 526) | simplest: stage transitions + per-stage percent |
| `utils.py` lines 465–1948 | `_update_phase_tracker_from_progress_text` (line 1141), `_apply_live_progress_text` (line 1823) | used by Pro Upscaler and Qwen Edit; phase-based rather than stage-based |

**What to build:** a new `workflow_progress.py` holding one tracker that all
three configure rather than reimplement. Suggested shape — adapt if the code
tells you otherwise:

- A `StageSpec` describing one stage (key, label, weight, optional total, whether
  it is enabled for this run).
- A `ProgressTracker` built from a list of `StageSpec`, exposing:
  - `observe_text(progress_text) -> None` — the single parser entry point
  - `overall_percent() -> int`
  - `render_panel() -> str` — HTML, or return a data structure and keep the HTML
    per-module if the three panels differ too much to unify
- Shared primitives that are currently duplicated near-verbatim: `_extract_node_id`,
  `_clamp_ratio`, `_progress_bar`, the `node=<id> <done>/<total>` and
  `Running node <id>: <label>` regexes, and the near-completion reconciliation.

**Method — do not rewrite from scratch:**

1. Read all three implementations fully before writing anything. Produce a table
   of behavioral differences (percent curves, stage weighting, what each does on
   a node it does not recognize, how each handles a stage cycling twice).
2. **Write characterization tests first.** For each of the three modules, feed
   realistic progress-text sequences through the *existing* function and assert
   the current outputs. Real samples live in `trace_logs/` if present; otherwise
   synthesize from the regexes and node constants. These tests are the contract:
   they must pass unchanged against the consolidated tracker.
3. Migrate one module at a time — Reference Generator first (simplest), then
   Pro Upscaler / Qwen Edit via `utils.py`, then General Enhancement (richest).
   Run the full suite after each.
4. Delete the old implementation only once its characterization tests pass
   against the shared tracker.

**Acceptance:** `workflow_progress.py` exists; all three modules use it; the
per-module tracker functions listed above are gone; characterization tests for
all three exist and pass; the percent sequence for a given input is identical to
before, per those tests.

If unifying the General Enhancement curve with the other two would change the
numbers users see, keep it configurable and say so in the commit message. Do not
silently change anyone's progress bar.

**Commit:** `Consolidate the three workflow progress trackers`

---

## Task 2 — Break up the four generator functions

**Why:** these are the functions that actually submit jobs, and none of them has
a single test, because none can be called without a live RunPod endpoint.

| Function | Location | Length |
|---|---|---|
| `fivek_generator` | `server_upscaler_with_flux_enhancement.py:224` | ~780 lines |
| `enhance_image` | `General_Enhancement_v04.py:1433` | ~630 lines |
| `reference_generator_generate` | `reference_generator.py:681` | ~500 lines |
| `flux2_klein_generate` | `flux2_klein_image_edit_9b_distilled.py` (see `_save_workflow_debug_json` below it) | ~340 lines |

All four are async generators that `yield` UI tuples as a job progresses. They
interleave five concerns:

1. **Validate + prepare** — check inputs, resize/encode images, build the payload
2. **Submit** — `RunpodAPI.run`, handle `RunpodSubmissionError` vs
   `RunpodSubmissionUncertainError`
3. **Poll** — status/stream loop, cancellation, terminal states
4. **Track progress** — feed text to the tracker, yield UI updates
5. **Finalize** — decode output, record the task, render before/after

**What to do:** extract 1, 2 and 5 into plain (mostly non-async) functions with
no Gradio types in their signatures, so they can be tested directly. Keep the
`yield`ing shell as the thin orchestrator over the extracted pieces. Do **not**
try to unify the four generators into one — their UI contracts differ. The goal
is testable seams, not a single mega-function.

Target: no function longer than ~150 lines, and every extracted piece callable
without a network.

**Then write the tests that were impossible before.** At minimum, per workflow:

- payload preparation puts the expected values on the expected node IDs
- the uncertain-submission path surfaces the "check the Jobs page" message and
  does not retry
- a terminal `FAILED` status produces the user-facing error, not a crash
- cancellation mid-poll stops polling and reports cancelled
- output decoding handles a missing/malformed `images` payload without raising

Mock at the `RunpodAPI` boundary — `tests/test_runpod_api_class.py` shows the
established pattern (patch `runpod_api_class.requests.request`).

**Method:** one workflow per commit, starting with the smallest
(`flux2_klein_generate`). Full suite green between each.

**Acceptance:** four generators under ~150 lines each; prepare/submit/finalize
extracted and unit tested; the app still runs a real job end to end (ask the
operator to confirm one job per workflow through the UI before the final commit).

**Commits:** `Extract testable stages from <workflow> job submission` ×4

---

## Task 3 — Split app.py

**Why:** ~3,300 lines doing six unrelated jobs.

Current landmarks:

- `EMBEDDED_HIDE_CSS` — lines 175–809, a 630-line CSS string literal
- RunPod billing client + renderers — ~1670–2058
- Plotly builders — ~2105–2440
- Admin HTML table renderers — ~2450–2630
- Gradio UI definition — `with gr.Blocks(...)` at 2810
- FastAPI server, proxies, auth gates — `_create_server_app` at 3116

**Target layout:**

```
static/app.css            # EMBEDDED_HIDE_CSS, read at startup
admin_render.py           # KPI cards, tables, rush-hour insights, plot builders
runpod_billing.py         # billing fetch + normalize + summarize + renderers
portal_proxy.py           # history & runpod-management proxies + auth gates
app.py                    # config, the Blocks UI, and wiring only
```

Notes:

- Load the CSS with `Path(__file__).parent / "static" / "app.css"` and read it at
  import. Keep `gr.Blocks(css=...)` working. Fail loudly if the file is missing.
- `portal_proxy.py` must keep `portal_auth.py` as its crypto layer.
  `tests/test_app_authorization.py` currently patches `app.HISTORY_PORTAL_SSO_SECRET`
  and `app.auth_service`; update those patch targets to wherever the functions
  land, and keep every assertion.
- The five near-identical `.click`/`.change` blocks at the end of `app.py` all
  pass the same 10 outputs. Collapse them into one loop over
  `(component, event_name)` pairs.
- Keep `if __name__ == "__main__"` in `app.py`.

**Acceptance:** `app.py` under ~800 lines; each new module imports cleanly on its
own; `import app; app._create_server_app()` still works; the proxy gate behavior
is unchanged — re-verify with the checks in the "Proxy gates" section below.

**Commit:** `Split app.py into config, rendering, billing, and server modules`

---

## Task 4 — Stop disabling TLS verification on the management proxy

`app.py:3293` (moves to `portal_proxy.py` if you do Task 3 first):

```python
async with httpx.AsyncClient(follow_redirects=True, timeout=60.0, verify=False) as client:
```

This proxies to `RUNPOD_MANAGEMENT_API_UPSTREAM_URL`, default
`https://127.0.0.1:8843`, which serves a self-signed cert from `openssl/`.
`verify=False` accepts any certificate.

**Fix:** verify against the local cert instead of disabling verification.

- Add `RUNPOD_MANAGEMENT_API_CA_BUNDLE`, defaulting to the existing
  `openssl/cert.pem` (see `_resolve_ssl_paths` in `app.py` for the pattern).
- Pass it as `verify=<path>` when the file exists.
- If it does not exist, log one clear warning naming the env var and the path
  tried — and still refuse to fall back to `verify=False`. A localhost proxy that
  cannot verify its upstream should fail loudly, not silently accept anything.
- Add a test asserting the client is constructed with a bundle path and never
  with `verify=False`.

**Acceptance:** no `verify=False` anywhere in the repo; the RunPod Management tab
still loads (ask the operator to confirm in the browser).

**Commit:** `Verify the management proxy upstream against the local cert`

---

## Proxy gates — re-verify after Tasks 3 and 4

Run this as a scratch script (not a committed test) and confirm every line
prints OK:

```python
import os, time
os.environ.setdefault("HISTORY_PORTAL_SSO_SECRET", "scratch-secret-value-long-enough-x")
from starlette.testclient import TestClient
import app

client = TestClient(app._create_server_app())
assert client.get("/history-proxy/", follow_redirects=False).status_code == 403
assert client.get("/history-proxy/assets/x.js", follow_redirects=False).status_code == 403
assert client.post("/history-proxy/api/tasks", json={}, follow_redirects=False).status_code == 403
assert client.get("/runpod-management/", follow_redirects=False).status_code == 403
assert client.get("/api/pods", follow_redirects=False).status_code == 403

email, exp, nonce = "admin.user@brickvisual.com", int(time.time()) + 600, "n1"
signed = {"email": email, "exp": str(exp), "nonce": nonce,
          "sig": app._history_portal_url_signature(email, exp, nonce)}
r = client.get("/history-proxy/", params=signed, follow_redirects=False)
assert r.status_code != 403
assert r.cookies.get(app.HISTORY_PORTAL_COOKIE_NAME)
print("gates OK")
```

Adjust the import paths if Task 3 moved these functions, but every assertion must
still hold.

---

## Reporting

After each task, report: what moved, net line delta, test count before/after, and
anything you found but deliberately did not fix. If a task turns out to be a bad
idea once you have read the code, stop and say why rather than forcing it — but
finish the other tasks.
