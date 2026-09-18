# GamesCrafters Status Page: Brainstorm and Rough Plan

## Context
GamesCrafters has no visibility into whether GamesmanUni and its solver backends are healthy. Problems (a solver process down, a corrupt or missing database) are found when a user hits them. We want an OpenAI-style status page: current state per component, plus daily history bars and uptime %.

Decisions so far:
- **Audience:** the GamesCrafters team (internal, so detail is welcome; but see the privacy caveat below).
- **Approach:** fully custom, not Uptime Kuma or Upptime.
- **Hosting:** the only machine is `nyc.cs.berkeley.edu`, and everything runs there, so the prober must run elsewhere. No budget, so **GitHub Actions** it is.

## Current architecture (from the repos)
```
GamesmanUni (Vue 3 SPA)
   -> GamesCraftersUWAPI (Flask, systemd, :8082)   [server.py, games/gamesman_*.py]
        -> GamesmanClassic :8083, GamesmanOne :8084, GamesmanPuzzles :9001, GamesmanPy :9004  (localhost only)
        -> external: Explainers GitHub repo (instructions), Lichess
```
UWAPI reaches the solvers over localhost, so an external prober cannot probe them directly. UWAPI has to report on them.

## Proposed design
Three pieces:

1. **`/health` endpoint in UWAPI** (runs on nyc; the only change outside the new repo)
   - Fans out to each solver on localhost and runs a **known-answer canary** per backend. Decision: **one tiny, stable game per solver**. Query the live API once, verify the answer by hand, and hardcode it as the expected value.
     - Candidates seen in `games/__init__.py` (unverified): Classic `0to10by1or2`; Puzzles `sevenpennies`; Py `stormyseas`; One a small game such as `gobbletg`, `neutron` or `quixo` (pick the smallest/fastest). The start position's expected `positionValue` and `remoteness` must be confirmed against the live API.
   - Returns JSON per component: `{status: ok|degraded|down, latency_ms, detail}`. `detail` is built server-side and sanitized: canary expected vs actual, error class, short truncated message. Full stack traces stay in nyc server logs.
   - This catches the failure that matters for a solver project: HTTP 200 with missing or corrupt solved data.
   - Public, no shared secret (the existing API is already public and unauthenticated, so a secret on `/health` alone would add little). Decision: **cache the result in memory in UWAPI for about 30 to 60 seconds** (store last result plus timestamp, reuse if recent) and keep canaries to one tiny query per backend. Repeated calls then cost the solvers nothing beyond one round of canaries per window. UWAPI has no caching or rate limiting today, so this is the first of either there. An optional token can be added later if abuse appears.

2. **Prober** (GitHub Actions cron, every 5 min)
   - Known URLs: frontend `https://nyc.cs.berkeley.edu/uni/`, API `https://nyc.cs.berkeley.edu/universal/v1/` (from GamesmanUni's `defaultApp.ts`). Note UWAPI's provider classes swallow backend errors and return `{}`, so HTTP 200 alone proves nothing; the known-answer canary is essential.
   - A Python script. Probes the public URLs the way real users do: the frontend page, UWAPI `/`, and `/health`. This also tests the proxy/TLS layer. Also probes external dependencies (Explainers repo, Lichess).
   - Status rules (decided): down = no response, error, or wrong canary answer; degraded = correct but slower than a latency threshold (start around 3s, tune from real data). A component's state only changes after **2 consecutive probes** agree, so one network blip doesn't flip the page.
   - Before building anything else, validate reachability: a throwaway workflow that curls the frontend and `/universal/v1/` from GitHub's runners, in case nyc or Berkeley blocks cloud IP ranges.
   - The script is host-agnostic, so it can move to a VPS later if we ever need 1-minute checks.

3. **Status page** (static, GitHub Pages)
   - Decided: plain HTML/JS with no build step, showing **90 days** of daily bars.
   - Fully automatic, no manual updates: the page is a fixed file whose JS fetches `current.json` and `daily.json` from the data branch on load (and re-fetches about every minute while open) and draws everything from that data. Days with no data render gray, not green.
   - Per component group: current state, daily green/yellow/red bars, uptime %.
   - Extras: latency chart, last failure reason.

**Component groups:** Website (GamesmanUni), API (UWAPI), Solvers (Classic, One, Puzzles, Py), External (Explainers, Lichess).

## Repo: new public `GamesCraftersStatus` in the GamesCrafters org
Why a new repo: it keeps the monitor's runs, commits and settings separate from the site it watches, and gives it its own clean Actions tab. Why public: public repos get unlimited free Actions minutes and free Pages; a private repo would exceed the 2,000 min/month free allowance (about 8,600 min/month at 5-minute runs).

```
GamesCraftersStatus/
├─ .github/workflows/probe.yml   # cron */5, runs the prober, commits results to `data` branch
├─ .github/workflows/rollup.yml  # daily: compact raw results into daily summaries (or fold into probe.yml)
├─ prober/                       # Python script and component/canary config
└─ site/                         # static status page (Pages)
```
Branches: `main` holds code and the site. `data` holds results only, so `main` history stays clean.

## Storage (no database server)
Results are plain JSON files committed to the `data` branch. There is no database to run, deploy or pay for. The workflow file is the entire deployment, and public-repo storage is free.

What is stored:
- **Raw checks:** one small file per run, e.g. `raw/2026-09-18/1205.json`, containing for each component `{status, latency_ms, sanitized detail}`. Roughly 10 components, about 1 KB per run.
- **Daily rollups:** `daily.json`, per component per day: number of checks, ok/degraded/down counts, avg and p95 latency, worst status. This drives the history bars and uptime %.
- **`current.json`:** latest status per component, overwritten each run.
- **Incidents (later):** `incidents.json`.

Compaction: raw files are kept for a rolling window (about 30 days), then the rollup job folds them into `daily.json` and deletes them. `daily.json` grows only about 10 rows a day, so it stays tiny for years. Occasionally squash the `data` branch history if the repo grows.

The page reads `current.json` and `daily.json` directly from `raw.githubusercontent.com` (CORS allows it; expect up to about 5 minutes of CDN caching), so Pages doesn't need to redeploy on every run.

When we'd outgrow this: 1-minute checks, per-game analytics or ad-hoc queries. Then move to SQLite on a VPS or a free-tier hosted DB. Not needed now.

## Mitigations for running scheduled workflows for years
- **Actions list flooding:** a dedicated repo with one probe workflow. Logs and artifacts expire after 90 days by default. Old runs can be bulk-deleted with `gh run delete` or a cleanup workflow if desired.
- **Commit noise and repo growth:** data goes on a separate `data` branch, raw files are compacted daily, and the branch can be squashed occasionally.
- **Best-effort scheduling:** GitHub may delay or skip cron runs. Treat missing intervals as "no data" in the rollup, not as "down", and don't promise exact uptime numbers.
- **60-day auto-disable:** GitHub disables scheduled workflows in public repos after 60 days of no repo activity. The prober's data commits should count as activity, but verify it. Add a safeguard, such as a monthly keepalive commit or a check that alerts if the last probe is older than X.
- **Org setup:** an org admin may need to enable Actions and Pages for the repo.

## Privacy and detail
The repo is public, so the `data` branch, the Pages site and the workflow logs are all publicly readable. That is acceptable: all GamesCrafters repos are already public, so paths, ports and service wiring are not secrets.
- Each probe records team-useful detail: status, latency, HTTP status code, canary expected vs actual, error class plus a short truncated message.
- Sanitize at the source: `/health` builds the detail string. Full stack traces stay in nyc server logs only.
- Never record credentials, tokens, environment values, request headers, client IPs or user data.
- The prober never prints raw response bodies to workflow logs.

## Separate follow-ups (not part of the status page)
- Rate limiting on the public UWAPI. None exists in the repo; check whether nyc has a reverse proxy (e.g. nginx) with limits, since that config isn't in the repos.
- `server.py` ends with `app.run(port=8082)` (Flask dev server) and the systemd service runs it directly, although `waitress` is in requirements.txt. Worth checking whether a production server should be used.

## Later phases
- **Alerts (decided: none in v1; Slack is a definite later addition):** Slack webhook on state change (down/degraded/recovered), reusing the 2-consecutive-probe rule so one failed probe doesn't alert anyone. Webhook URL stored as a GitHub Actions secret. Add a dead-man check: alert if no probe has run in a while. Design the prober so state changes are already detected and recorded in v1, so adding the Slack call later is a small change.
- **Nightly golden-list run:** many `(game, variant, position, expected value, remoteness)` cases across games, to flag `unsolved` or wrong results. This catches broken data for one game that the single canary misses.
- **Incidents:** a simple file-based incident log shown on the page.

## Phases and steps (rollout decided: phased, test before announcing)
We work one step at a time, and nothing outward-facing (creating the org repo, pushing, deploying to nyc, enabling schedules) happens without an explicit go-ahead. Current situation: nyc is completely unreachable (DNS resolves to 128.32.153.232 but ports 22, 80 and 443 all time out), so Phase A is done first because it needs no server.

### Phase A: build locally, no nyc needed
Everything here is developed and tested locally with fake data. Nothing is pushed or deployed.

- **A1. Define the data contracts** (the shared format for prober, rollup and page). Write example JSON and a short spec for:
  - the `/health` response: per component `{status, latency_ms, detail}`
  - a raw run file `raw/<date>/<HHMM>.json`
  - `current.json` (latest state per component, plus the pending "1 of 2" state for the debounce)
  - `daily.json` (per component per day: counts of ok/degraded/down, avg and p95 latency, worst status)
  - Done when: all four shapes are written down with examples and everyone agrees on field names and on UTC as the day boundary.
- **A2. Scaffold the status repo locally** in the layout from the "Repo" section (`prober/`, `site/`, `.github/workflows/`), as a local git repo only. Done when: the folder structure exists, with a README explaining the pieces.
- **A3. Write the prober script** (Python, config-driven).
  - Config file lists each component: URL, check type (liveness or canary), expected answer, latency threshold. Canary expected values are placeholders until Phase B.
  - Probes with a timeout, measures latency, assigns ok/degraded/down.
  - Applies the 2-consecutive-probe rule using the previous `current.json`.
  - Writes the raw run file and updates `current.json`. Never prints raw response bodies.
  - Test 1: point it at the real, currently dead nyc and confirm it records `down` correctly (a free failure-path test).
  - Test 2: point it at a small local fake server that returns ok, slow, wrong-answer and error, and confirm each maps to the right status and the debounce works.
  - Done when: both tests pass.
- **A4. Write the rollup/compaction job.** Folds raw files into `daily.json`, deletes raw files older than about 30 days, treats missing intervals as "no data". Test with generated fake data spanning more than 30 days, including gaps. Done when: `daily.json` matches hand-computed numbers and old raw files are removed.
- **A5. Build the static status page** (plain HTML/JS, no build step) against sample JSON.
  - Overall banner, component groups, current state, 90 daily bars (gray for no data), uptime %, latency chart, last failure reason.
  - Re-fetches the JSON about every minute; must work at phone width.
  - Test with sample data covering all-green, an outage, a degraded day, missing days, and a long outage.
  - Done when: it renders correctly for all those cases in a browser.
- **A6. Write the workflows** (`probe.yml` on `*/5`, `rollup.yml` daily, with a `concurrency` group, shallow checkout of the `data` branch, pushing to `data`), kept unpushed and unscheduled. Done when: the YAML is reviewed and lint-clean.
- **A7. Write `/health` in a local UWAPI branch** (in GamesCraftersUWAPI): the route, per-backend canary calls through the existing provider classes, the sanitized `detail` string, 30 to 60 second in-memory caching, and a per-backend timeout so one hung solver can't hang the endpoint. Test with mocked providers only. Done when: unit-style tests cover ok, slow, wrong answer, error and cache-hit cases.

### Phase B: needs nyc back up
- **B1. Confirm nyc is reachable** again (someone with access restores the machine). Re-run my curl and port checks from this machine.
- **B2. Reachability from GitHub.** Throwaway workflow in a personal public repo curls `/uni/` and `/universal/v1/` from GitHub's runners, run a few times at different hours. Done when: consistent HTTP 200. If it fails, stop and rethink where the prober runs.
- **B3. Verify canary values.** Read-only GETs against the live API for the chosen game per solver. Confirm the start position's `positionValue` and `remoteness` by hand, then fill them into the prober config and the `/health` code. Done when: all four solvers have a verified canary.
- **B4. Test `/health` for real** on a local or dev UWAPI run against the live solvers, including stopping one solver to confirm `down`. Then, with go-ahead and someone with nyc access, deploy the change (systemd service restart) and confirm `/universal/v1/health` responds and caches.
- **B5. Point the prober at the real `/health`** and run it locally end to end (prober, rollup, page) against live data.

### Phase C: go live quietly
- **C1. Create the public `GamesCraftersStatus` repo** in the GamesCrafters org (needs the repo name decision and org admin to enable Actions and Pages). Push `main`, create the `data` branch, enable Pages.
- **C2. Turn on the schedule.** Push the workflows, run `probe.yml` manually once, confirm a raw file and `current.json` land on `data`, then let the cron run.
- **C3. Verification checklist** (see the Verification section): induced failures, slow response, nyc-unreachable outage, rollup correctness, schedule stays alive.
- **C4. Quiet burn-in, about one week.** Check accuracy, watch for false alarms or flapping, tune the latency threshold from real timings, confirm the schedule keeps running and data volume matches expectations.
- **C5. Announce.** Share the link with the team and add it to the GamesmanUni README next to the existing shields.io site-status badge.

### Phase D: later
- **D1. Keepalive and dead-man check.** Monthly keepalive commit and a check for a stale `current.json` (no probe in a long time).
- **D2. Slack alerts.** Webhook on state changes (down, degraded, recovered), webhook URL in GitHub Actions secrets, reusing the state-change detection already built into the prober.
- **D3. Nightly golden-list run** across many games and variants.
- **D4. Incident log** shown on the page.
- **Separate from the status page:** rate limiting on the public UWAPI, and the `app.run` vs `waitress` question.

## Open questions
- Who is on point for `/health` in UWAPI, and who owns the status repo long term? (Canary choice is decided; the exact expected values are verified during phase 1.)
- Repo name and Pages URL (org Pages, or a subdomain later)?
- Confirm the team is fine with a public status page that includes sanitized per-probe detail (all repos are already public).
- (Later, for the Slack alerts phase) Which Slack channel and who creates the webhook?

## Verification (once built)
- Kill a solver process on a dev/nyc instance, confirm `/health` marks it `down` and the page turns red within one or two probe intervals.
- Point a canary at a wrong expected value, confirm it reports `down` even though HTTP is 200.
- Add an artificial delay, confirm `degraded`.
- Make nyc unreachable, confirm the page still loads and shows the outage.
- Run the rollup on sample data and confirm raw files are compacted and `daily.json` is correct.
- Leave the repo untouched for a while (or simulate) and confirm the schedule stays enabled.
