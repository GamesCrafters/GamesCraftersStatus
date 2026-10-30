# GamesCrafters Status: Vision, Spec and Plan

*Rewritten 2026-10-29, after nyc came back. Supersedes the September plan, which was written while nyc was unreachable. The data contract is in [DATA_FORMATS.md](DATA_FORMATS.md).*

## 1. Vision

A **Status page inside GamesmanUni** at `https://nyc.cs.berkeley.edu/uni/status`, linked from the site header. Anyone on the team, and any curious user, can see at a glance:

- whether the website, the API and each solver backend are working right now,
- a 90-day history per component (one bar per day: green, yellow, red, or gray for no data),
- uptime %, latency over time, and the reason for the last failure.

The page looks like the rest of Uni: same header, same themes, same i18n. The checks run **outside** nyc (GitHub Actions), so an outage of nyc is recorded even though nyc can't report it.

Later, Uni can use the same data to warn users in place ("GamesmanPuzzles is having trouble right now") on the pages that depend on a broken solver, and the team gets Slack alerts.

### What it is not
- Not a public SLA. GitHub's scheduler is best-effort, so the numbers are approximate.
- Not per-game correctness testing. One canary per solver catches "solver down" and "solved data missing". A broad nightly golden-list run is a later phase.
- Not monitoring for third-party services (Lichess) or for in-process UWAPI games (Nim, Kayles, ...).

## 2. What changed from the September plan, and why

| September plan | New plan | Why |
|---|---|---|
| New `/health` endpoint in UWAPI runs the solver canaries | **The prober runs the canaries itself through the public API.** `/health` moves to a later, optional phase | Confirmed live: UWAPI already proxies every solver publicly (`/<game>/<variant>/positions/?p=...`). Probing through it tests the real user path (nginx, UWAPI, solver, database) and needs **zero changes or deploys on nyc** to ship v1. |
| Standalone status site on GitHub Pages | **A `/status` page in GamesmanUni** is the main surface, plus a tiny fallback page on GitHub Pages | The goal is to have it live on Uni. The fallback covers the one case Uni can't: nyc itself being down. |
| Canary values unverified | **Verified against the live API on 2026-10-29** (section 4) | nyc is back. |
| Build the page first, then collect data | **Start collecting data early, build the page against real history** | History bars need history. Turning the prober on weeks before the page ships means the page launches with real data instead of 90 gray bars. |

Kept from before: GitHub Actions on a public repo, JSON files on a `data` branch (no database), the retry-before-recording rule, degraded = correct but slow, gray = no data (never counted as down), and the decisions already recorded in DATA_FORMATS.md.

## 3. Architecture

```
 GitHub Actions (hourly)               nyc.cs.berkeley.edu (unchanged for v1)
 ┌──────────────────────────┐          ┌───────────────────────────────────────────┐
 │ prober (Python)          │─ HTTPS ─▶│ nginx                                     │
 │  - GET /uni/ + its JS    │          │  ├─ /uni/        GamesmanUni (static SPA)  │
 │  - GET /universal/v1/    │          │  └─ /universal/v1/  UWAPI (Flask :8082)    │
 │  - 4 solver canaries     │          │        ├─ GamesmanClassic  :8083           │
 │  - 1 instructions check  │          │        ├─ GamesmanOne      :8084           │
 │ retry, write JSON        │          │        ├─ GamesmanPuzzles  :9001           │
 └────────────┬─────────────┘          │        ├─ GamesmanPy       :9004           │
              │ git push               │        └─ Explainers repo (instructions)   │
              ▼                        └───────────────────────────────────────────┘
 GamesCraftersStatus repo, `data` branch
   current.json, daily.json, raw/...
              │  fetched by the browser from raw.githubusercontent.com
              │  (CORS: *, cache max-age=300, both confirmed)
      ┌───────┴─────────────────────────┐
      ▼                                 ▼
 Uni /status page (main)        Fallback page on GitHub Pages
 (Vue, in GamesmanUni)          (plain HTML, works when nyc is down)
```

Three repos are involved:
- **GamesCraftersStatus** (new, public, GamesCrafters org): prober, workflows, the `data` branch, the fallback page.
- **GamesmanUni**: the `/status` page and header link.
- **GamesCraftersUWAPI**: no change for v1. Later: `/health` and request timeouts (section 10).

## 4. Components and checks

Seven components, in three groups. Ids are permanent.

| id | Group | Display name | Check (all GETs, 60 s timeout) | ok when |
|---|---|---|---|---|
| `frontend` | Website | GamesmanUni | `/uni/`, then the main `/uni/assets/index.*.js` it references | both 200; HTML contains `<title>GamesmanUni</title>` and `id="app"` |
| `api` | API | Universal Web API | `/universal/v1/` | 200, JSON list with at least 50 games (112 today) |
| `instructions` | API | Game instructions | `/universal/v1/0to10by1or2/regular/instructions/?locale=en` | 200 and `instructions` starts with `# 0 to 10 by 1 or 2` (UWAPI returns `""` on any failure, so 200 alone proves nothing) |
| `solver-classic` | Solvers | GamesmanClassic | canary below | value and remoteness match, `moves` non-empty |
| `solver-one` | Solvers | GamesmanOne | canary below | same |
| `solver-puzzles` | Solvers | GamesmanPuzzles | canary below | same |
| `solver-py` | Solvers | GamesmanPy | canary below | same |

**Canaries** (verified live 2026-10-29; all answered in 40–150 ms from Berkeley):

| Solver | Game / variant | Position `p` | Expected | Notes |
|---|---|---|---|---|
| Classic | `0to10by1or2/regular` | `1_0` | `win`, remoteness 7 | Verified by hand: P-positions are 1, 4, 7, 10, so 0→1→…→10 takes 7 moves. |
| One | `quixo/3x3` | `1_---------` | `win`, remoteness 7 | Smallest GamesmanOne game. |
| Puzzles | `sevenpennies/regular` | `00000000_9` | `win`, remoteness 14 | |
| Py | `stormyseas/beg1` | `oowowwwowowwwooowrwowpwwRwwoPwwwowo7` | `win`, remoteness 3 | |

URL form: `/universal/v1/<game>/<variant>/positions/?p=<url-encoded position>`. Canaries live in the prober config, not in code, so they can be changed without touching logic. If a canary game is ever removed or re-solved on purpose, update the config in the same PR.

**Load on nyc:** 7 to 8 small GETs an hour (a few more on a retry), under 200 a day. Negligible.

## 5. Status rules

- **down:** no response, timeout (60 s), non-2xx, unparseable body, or a wrong answer (canary mismatch, empty instructions, missing marker).
- **degraded:** the answer is correct but latency is above the component's threshold. Starting threshold is **30,000 ms** for every component, and anything over a minute is down. This is deliberately very loose: usage is a handful of users at a time, so a slow solver is fine, and only an extremely slow answer should turn yellow. (Idle latency is 40–300 ms.) Tune after burn-in.
- **ok:** otherwise.
- **Schedule and retry:** the prober runs **hourly**. If a component is not `ok`, it is re-checked once, 90 s later, in the same run, and the retry result is what gets recorded, so one blip never reaches the page and a real problem is confirmed within a single run (about an hour after it starts at worst). Table of outcomes in DATA_FORMATS.md. A club-level site with few users does not need faster, and 3–5 checks a day would be too sparse to be useful (outages could fall between checks, history would be thin).
- **No data is never down.** Skipped or delayed GitHub runs show as gray and are left out of uptime.
- **If nyc is entirely unreachable,** every component is `down` with its own real reason (e.g. `ConnectTimeout`). No separate "unknown" state.
- **overall** = the worst status of any component.

## 6. The Uni `/status` page (main surface)

### Route and navigation
- Route `/status` → `TheStatusPage.vue` in `src/components/views/`. It must be declared **before** the `/:type` catch-all in `routes.ts` (otherwise `/status` is read as a game type). nginx already serves the SPA for unknown paths: `/uni/status` returns 200 today.
- Header link "Status" in `AppHeaderNavigations.vue`, with a small colored dot for `overall` (a later step; v1 can ship with a plain link).
- All strings go through vue-i18n like the rest of the header.

### Layout (top to bottom)
1. **Overall banner:** "All systems operational" / "Some systems degraded" / "Partial outage", plus "Last checked 3 min ago" from `updatedAt`. If `updatedAt` is more than 2 hours old, show "Status data may be stale".
2. **Groups** (Website, API, Solvers), each with its components. Per component:
   - name, current status pill, "since <relative time>",
   - 90 daily bars (oldest left, today right) colored by the day's worst status, gray for no data. Hover or tap shows the date, uptime % and counts.
   - "99.8% uptime (90 days)",
   - expandable details: latency now, last failure time and reason.
3. **Latency chart:** daily p95 per component over 90 days (solvers on one chart is fine).
4. **Footer note:** "Checked hourly from GitHub Actions. Times are UTC. Gray means no data." Link to the fallback page and the repo.

### Behavior
- Fetches `current.json` and `daily.json` from `https://raw.githubusercontent.com/GamesCrafters/GamesCraftersStatus/data/` on mount, re-fetches every 60 s while the page is visible, and stops when hidden.
- If the fetch fails, keep the last data and show "Couldn't refresh status" (never a blank page).
- Reads `schemaVersion`. If it is higher than the page knows, show a soft warning and render what it can.
- Uses Uni's theme CSS variables (`--gu-*`) so it works in all 11 themes. Status colors come from one small palette checked for contrast in light and dark themes, and status is never shown by color alone (pill text, tooltip text).
- Works at phone width: bars shrink to 30 days below about 600 px.
- No new dependencies. The bars and the latency chart are plain SVG.
- PWA note: Uni's service worker has no runtime caching for cross-origin requests, so status data is always fetched fresh. Returning visitors may still get the Uni shell from the service-worker cache during an nyc outage, but first-time visitors won't, which is why the fallback page exists.

### Fallback page (GitHub Pages, in this repo)
One plain HTML file in `site/` that renders the overall banner and the current status of each component from `current.json`, with "Last checked" time. No history bars. It must work with nyc down. It is linked from the Uni page's footer and pinned in the team's channel.

## 7. Data and storage

The data contract is in [DATA_FORMATS.md](DATA_FORMATS.md). In short:
- `data` branch: `raw/YYYY-MM-DD/HHMMSS.json` (one per run, kept 30 days), `current.json` (latest state, overwritten), `daily.json` (per component per UTC day; today's entry recomputed every run).
- `main` branch: code, config, workflows, fallback page.
- The browser reads `data` through raw.githubusercontent.com (up to 5 min of CDN caching, so the page is at most about 1 hour and 5 minutes behind reality).

## 8. Operations

- **Workflows:** `probe.yml` (cron `7 * * * *`, hourly at 7 past, plus manual trigger; avoiding the top of the hour, when GitHub is busiest) and `cleanup.yml` (daily, deletes raw files older than 30 days). Both use the same `concurrency` group so they never push at the same time. Pushes retry with `git pull --rebase`.
- **Repo:** public, so Actions minutes are free (a private repo would use its free minutes up faster).
- **60-day auto-disable:** GitHub disables cron in public repos after 60 days without repo activity. Data commits should count. Verify during burn-in, and add a monthly keepalive as a safety net.
- **Data branch growth:** about 300 small commits a day. Squash the `data` branch history yearly if clone size matters (the page only reads the latest files).
- **Privacy:** everything in the repo, logs and data is public. Store only status, latency, HTTP code and short sanitized details (expected vs. got, error class). Never response bodies, headers, IPs or secrets. The prober never prints response bodies.
- **Ownership:** a named owner for the Status repo and one for the Uni page (open question 1).

## 9. Plan

Work goes one step at a time. Every **[GO]** step is outward-facing (creates a repo, pushes, enables a schedule, deploys to nyc) and waits for an explicit go-ahead. Everything else is local.

### Phase 0: Prove the riskiest assumption (about 1 day)
- **0.1 [GO] Reachability from GitHub runners.** A throwaway `workflow_dispatch` workflow in a personal public repo that curls `/uni/`, `/universal/v1/` and one canary URL and prints only status code and timing. Run it 3–4 times at different hours. **Done when:** consistently 200. **If it fails** (Berkeley blocking cloud IPs), stop and rethink where the prober runs (options: a team member's always-on machine, a free-tier VM, or the `/health` endpoint with a push model).

### Phase 1: Prober, locally against live nyc (about 1 week)
- **1.1 Finalize DATA_FORMATS.md** (drop the DRAFT label). Done when the team agrees on it.
- **1.2 Prober config** `prober/config.json`: the components, URLs, canaries, thresholds and groups from section 4.
- **1.3 Prober script** `prober/probe.py` (Python 3, `requests` only). Runs the checks one at a time with timeouts (in parallel they inflate each other's latency), applies the status rules and the in-run retry, writes the raw file, `current.json` and today's `daily.json` entry. Takes a `--data-dir` argument so it runs against a local folder.
- **1.4 Tests** (`pytest`, a local fake server): ok, slow (degraded), wrong canary, empty instructions, 500, timeout, connection refused; retry (a blip that passes on retry is recorded ok, two failures are recorded down, only non-ok components are re-checked); `since`/`lastFailure` bookkeeping; daily recompute including gaps; UTC day rollover.
- **1.5 Live run:** run the prober locally against real nyc for a few hours (a loop every few minutes) and check that the files look right and nothing reports `down` falsely.
- **1.6 Cleanup script** `prober/cleanup.py` (delete raw days older than 30) with a test.
- **Done when:** tests pass and a live local run produces correct files.

### Phase 2: Go live, collecting data only (about 1–2 weeks of burn-in)
- **2.1 Workflows** `probe.yml` and `cleanup.yml`, linted, reviewed.
- **2.2 [GO] Create `GamesCrafters/GamesCraftersStatus`** (public; an org admin may need to enable Actions). Push `main`, create the `data` branch.
- **2.3 [GO] Turn on the schedule.** Trigger once manually, confirm files land on `data`, then let the cron run.
- **2.4 Burn-in.** Watch for false alarms and flapping, measure real GitHub-to-nyc latency and set thresholds, measure how often GitHub skips runs, confirm the schedule stays enabled.
- **2.5 Induced-failure checks** that need no nyc access: point one canary at a wrong expected value in a branch run, and point one URL at a dead host. Confirm `down` when the retry also fails, and recovery on the next good run.
- **Done when:** two weeks of data (about 340 hourly checks) with no false `down` and thresholds tuned. Hourly checks need longer than 5-minute ones to show the same amount of behavior.

### Phase 3: Uni `/status` page (about 1–2 weeks, overlaps with burn-in)
- **3.1** Branch in GamesmanUni (see open question 3 for the base branch). Add the route, `TheStatusPage.vue`, a small `statusApi.ts` fetcher, the i18n strings and the header link.
- **3.2** Build it against the live `data` branch, plus a local fixture set covering: all green, an outage, a degraded day, gray days, a long outage, stale data, and fetch failure.
- **3.3** Check all themes, phone width, and keyboard/screen-reader basics (pills have text, bars have labels).
- **3.4 Fallback page** in `site/` of the Status repo.
- **Done when:** `yarn build` passes, every fixture renders correctly, and the team has reviewed it locally (`yarn preview`).

### Phase 4: Ship on Uni
- **4.1 [GO]** Open a PR to GamesmanUni, get review, merge.
- **4.2 [GO] Deploy** to nyc the usual way (`yarn build`, copy `dist` to the server; needs whoever does Uni deploys). Confirm `https://nyc.cs.berkeley.edu/uni/status` works.
- **4.3 [GO]** Enable GitHub Pages for the fallback page.
- **4.4** Announce to the team, and add a status badge or link to the GamesmanUni README.

### Phase 5: Later (each independent, in suggested order)
- **5.1 Slack alerts** on state changes (down, degraded, recovered), webhook in an Actions secret, plus a dead-man alert if `current.json` is older than 2 hours.
- **5.2 Header status dot** and **in-context warnings** in Uni: a small banner on game pages whose solver is currently down.
- **5.3 Health endpoints (drafted 2026-10-29, local branches, not pushed).** The existing endpoints were inaccurate: every solver's `/health` reported `ok` from CPU and memory of its own HTTP process without touching solved data, returned 503 for "busy", and read CPU as 0.0 after restarts. UWAPI's `/health/` only relayed those, and `/games/health/` fanned out to about 700 backend requests per call with timeout handling that could never fire. Drafted fixes:
  - **Solvers** (`fix/health-liveness` in GamesmanClassic, One, Puzzles, Py): `/health` is honest liveness only: always 200 with `status`, `uptimeSeconds`, `rssMb` (Classic also `gameProcesses`). Classic also accepts `/health/` and query strings.
  - **UWAPI** (`feature/health-endpoint`, in the `GamesCraftersUWAPI-health` worktree): `/health/` runs the four canaries through the providers in parallel with a 50 s limit and a 30 s cache, and reports per-solver `status`/`latencyMs`/`detail` (contract in DATA_FORMATS.md). `/games/health/` is removed from the public API; its idea moves to 5.4. Solver provider requests get a 60 s timeout (today a hung solver holds that request and its thread forever). Tests in `tests/test_health.py`. `master` is already merged into the branch.
  - **Once deployed,** the prober can read `/universal/v1/health/` for better failure details than "got nothing". Until then the prober's own canaries are enough.
- **5.4 Nightly golden-list run** across many games and variants to catch broken data for individual games.
- **5.5 Incident notes** (a hand-written `incidents.json` shown on the page).
- **5.6** `recent.json` with the last 24 h of per-probe latency for a finer chart.

## 10. Open questions

1. **Owners:** who owns the Status repo and the prober long term, and who reviews and deploys the Uni change?
2. **Org repo:** is the name `GamesCraftersStatus` OK, and which org admin enables Actions and Pages?
3. **Uni base branch:** the `/status` page should be built on `main` (what's deployed) or on `ui-update` (the redesign in progress, which introduces the `--gu-*` theme variables)? This affects styling and when it can ship.
4. **Public detail:** is the team fine with failure details (e.g. "canary mismatch: expected win/7, got nothing") being public? All repos are already public, so I'd say yes.
5. **Slack (Phase 5):** which channel, and who creates the webhook?

## 11. Verification checklist (before announcing)

- [ ] GitHub runners reach nyc consistently (0.1).
- [ ] A wrong canary shows `down` when the retry also fails and recovers on the next good run (2.5).
- [ ] A dead host shows `down` with a clear reason (2.5).
- [ ] A slow response shows `degraded` (fake server test, 1.4).
- [ ] Skipped runs show gray, not red, and don't lower uptime (1.4, 2.4).
- [ ] `daily.json` matches hand-computed numbers for a fixture day (1.4).
- [ ] The schedule stayed enabled for the whole burn-in (2.4).
- [ ] `/uni/status` renders in all themes and at phone width, and survives a failed fetch (3.2, 3.3).
- [ ] The fallback page loads and shows status with nyc unreachable (simulate by pointing it at fixture data).
