# Data formats

This is the shared contract between the prober config, the prober, the cleanup job, the Uni `/status` page and the fallback page. Change it here first, then change the code. See [PLAN.md](PLAN.md) for the why.

**Status: DRAFT for final review (plan step 1.1).** Canary values were verified against the live API on 2026-10-29. Latencies in examples are illustrative.

## Conventions

- All timestamps are ISO 8601 in UTC with a trailing `Z`, e.g. `2026-10-29T19:05:12Z`.
- A "day" is a **UTC** calendar day, written `YYYY-MM-DD`. The page labels bars as UTC.
- Every data file has `"schemaVersion": 1`. Bump it on any breaking change. Readers show a soft warning for a higher version and render what they can.
- Status values are exactly `ok`, `degraded`, `down` (lowercase).
- Detail strings are short (max about 200 characters), plain text, and sanitized: no response bodies, stack traces, tokens, headers or IPs.

## Components

Ids are stable and never renamed. The page gets display names and grouping from `current.json`, so it needs no separate config.

| id | Group | Name | What the prober checks |
|---|---|---|---|
| `frontend` | `website` | GamesmanUni | `GET /uni/` is 200 and contains `<title>GamesmanUni</title>` and `id="app"`; the main `/uni/assets/index.*.js` it references is 200 |
| `api` | `api` | Universal Web API | `GET /universal/v1/` is 200 and a JSON list of at least 50 games |
| `instructions` | `api` | Game instructions | `GET /universal/v1/0to10by1or2/regular/instructions/?locale=en` is 200 and `instructions` starts with `# 0 to 10 by 1 or 2` |
| `solver-classic` | `solvers` | GamesmanClassic | canary (see config) |
| `solver-one` | `solvers` | GamesmanOne | canary |
| `solver-puzzles` | `solvers` | GamesmanPuzzles | canary |
| `solver-py` | `solvers` | GamesmanPy | canary |

Groups, in display order: `website` (Website), `api` (API), `solvers` (Solvers).

Not monitored: the Lichess tablebase (third-party), and games UWAPI computes in-process (Nim, Kayles, Dawson's Chess, ...).

## 1. Prober config: `prober/config.json` (on `main`)

The only place URLs, canaries and thresholds live.

```json
{
  "baseUrl": "https://nyc.cs.berkeley.edu",
  "timeoutMs": 60000,
  "defaultDegradedMs": 30000,
  "retryDelaySeconds": 90,
  "groups": [
    { "id": "website", "name": "Website" },
    { "id": "api",     "name": "API" },
    { "id": "solvers", "name": "Solvers" }
  ],
  "components": [
    { "id": "frontend", "group": "website", "name": "GamesmanUni",
      "check": "frontend", "path": "/uni/", "markers": ["<title>GamesmanUni</title>", "id=\"app\""] },
    { "id": "api", "group": "api", "name": "Universal Web API",
      "check": "gameList", "path": "/universal/v1/", "minGames": 50 },
    { "id": "instructions", "group": "api", "name": "Game instructions",
      "check": "instructions", "path": "/universal/v1/0to10by1or2/regular/instructions/?locale=en",
      "startsWith": "# 0 to 10 by 1 or 2" },
    { "id": "solver-classic", "group": "solvers", "name": "GamesmanClassic",
      "check": "canary", "game": "0to10by1or2", "variant": "regular", "position": "1_0",
      "expect": { "positionValue": "win", "remoteness": 7 } },
    { "id": "solver-one", "group": "solvers", "name": "GamesmanOne",
      "check": "canary", "game": "quixo", "variant": "3x3", "position": "1_---------",
      "expect": { "positionValue": "win", "remoteness": 7 } },
    { "id": "solver-puzzles", "group": "solvers", "name": "GamesmanPuzzles",
      "check": "canary", "game": "sevenpennies", "variant": "regular", "position": "00000000_9",
      "expect": { "positionValue": "win", "remoteness": 14 } },
    { "id": "solver-py", "group": "solvers", "name": "GamesmanPy",
      "check": "canary", "game": "stormyseas", "variant": "beg1",
      "position": "oowowwwowowwwooowrwowpwwRwwoPwwwowo7",
      "expect": { "positionValue": "win", "remoteness": 3 } }
  ]
}
```

- A canary requests `/universal/v1/<game>/<variant>/positions/?p=<url-encoded position>` and passes when `positionValue` and `remoteness` match and `moves` is a non-empty list.
- A component may set its own `degradedMs` to override `defaultDegradedMs`.
- Display order of components within a group is their order in this file.

**Detail strings** the prober writes:

| Situation | `detail` |
|---|---|
| pass | `ok` |
| correct but slow | `ok, slow (31412 ms > 30000 ms)` |
| canary mismatch | `canary mismatch: expected win/7, got lose/3` (or `got nothing` when the fields are missing) |
| content check failed | `marker missing`, `asset 404`, `empty instructions`, `only 12 games` |
| HTTP error | `HTTP 502` |
| no response | `<ErrorClass>`, e.g. `ConnectTimeout`, `ReadTimeout after 60000 ms`, `ConnectionError` |
| bad body | `invalid JSON` |

## 2. Raw run file: `raw/YYYY-MM-DD/HHMMSS.json` (on `data`)

One file per probe run, named by the run's UTC start time. Kept 30 days, then deleted by the cleanup job.

```json
{
  "schemaVersion": 1,
  "startedAt": "2026-10-29T19:05:12Z",
  "components": {
    "frontend":       { "status": "ok",       "latencyMs": 210,   "httpStatus": 200,  "detail": "ok" },
    "api":            { "status": "ok",       "latencyMs": 305,   "httpStatus": 200,  "detail": "ok" },
    "instructions":   { "status": "ok",       "latencyMs": 480,   "httpStatus": 200,  "detail": "ok" },
    "solver-classic": { "status": "ok",       "latencyMs": 142,   "httpStatus": 200,  "detail": "ok" },
    "solver-one":     { "status": "degraded", "latencyMs": 31412, "httpStatus": 200,  "detail": "ok, slow (31412 ms > 30000 ms)",
                        "retried": true, "firstStatus": "degraded" },
    "solver-puzzles": { "status": "ok",       "latencyMs": 120,   "httpStatus": 200,  "detail": "ok",
                        "retried": true, "firstStatus": "down" },
    "solver-py":      { "status": "down",     "latencyMs": null,  "httpStatus": null, "detail": "ReadTimeout after 60000 ms",
                        "retried": true, "firstStatus": "down" }
  }
}
```

Fields per component:

- `status`: the final status of this run, after the retry rule below. This is what the page and uptime use.
- `retried`: present and `true` only if the first check was not `ok` and the component was checked again.
- `firstStatus`: present only with `retried`. What the first check saw, kept so blips stay visible in the raw data.
- `latencyMs`, `httpStatus`, `detail`: from the check that decided `status` (the retry, if there was one). `latencyMs` is `null` if there was no response; for `frontend` it is the sum of both requests.

**Retry rule.** The schedule is hourly, so a bad check is confirmed inside the same run instead of waiting for the next one:

1. Run every check once, in parallel.
2. If any component is not `ok`, wait `retryDelaySeconds` (90) and check **only those components** again.
3. The retry result becomes `status`. In the example, `solver-puzzles` failed once but was fine on the retry, so it is recorded `ok` (with `firstStatus: "down"` for the record), while `solver-py` failed twice and is recorded `down`.

| First check | Retry | `status` |
|---|---|---|
| ok | none | ok |
| down or degraded | ok | ok |
| down | down | down |
| degraded | degraded | degraded |
| down | degraded | degraded |
| degraded | down | down |

There is no state carried between runs for this: each run decides on its own. A skipped run just leaves a gap (shown gray).

## 3. `current.json` (on `data`, overwritten every run)

Latest state plus everything the page needs to draw itself.

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-10-29T19:05:15Z",
  "overall": "ok",
  "groups": [
    { "id": "website", "name": "Website", "components": ["frontend"] },
    { "id": "api",     "name": "API",     "components": ["api", "instructions"] },
    { "id": "solvers", "name": "Solvers", "components": ["solver-classic", "solver-one", "solver-puzzles", "solver-py"] }
  ],
  "components": {
    "solver-puzzles": {
      "name": "GamesmanPuzzles",
      "group": "solvers",
      "status": "down",
      "since": "2026-10-29T19:05:12Z",
      "lastCheckedAt": "2026-10-29T19:05:12Z",
      "latencyMs": 95,
      "detail": "HTTP 500",
      "lastFailure": { "at": "2026-10-29T19:05:12Z", "detail": "HTTP 500" },
      "lastStateChange": { "at": "2026-10-29T19:05:12Z", "from": "ok", "to": "down" }
    }
  }
}
```

(Only one component is shown; the real file has all seven.)

Fields:

- `overall`: the worst `status` across all components.
- `groups`: display order and membership.
- `status`: the current status (the last run's final `status`). `since`: when this status began.
- `lastFailure`: the most recent check that saw `down` (or `null`), including first checks that passed on retry, kept across runs so the page can show the last failure reason even for blips.
- `lastStateChange`: the most recent change of `status` (or `null`). Slack alerts (plan 5.1) fire exactly when this changes.
- `detail` and `latencyMs`: from the most recent probe.

## 4. `daily.json` (on `data`)

Per component, per UTC day, counting each run's final `status`.

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-10-29T19:05:15Z",
  "components": {
    "api": {
      "2026-10-28": { "checks": 24, "ok": 24, "degraded": 0, "down": 0, "avgLatencyMs": 301, "p95LatencyMs": 470, "worst": "ok" },
      "2026-10-29": { "checks": 19, "ok": 17, "degraded": 1, "down": 1, "avgLatencyMs": 320, "p95LatencyMs": 610, "worst": "down" }
    }
  }
}
```

Rules:

- `checks` = `ok` + `degraded` + `down`. A full day is 24 checks (hourly); fewer means GitHub skipped or delayed runs. Retries do not add checks: each run counts once, with its final `status`.
- **No data:** a missing day, or `checks: 0`, is drawn gray. Missed runs are never counted as `down`.
- `worst`: the worst status that day. It sets the bar color: red if any `down`, else yellow if any `degraded`, else green.
- **Uptime %** over a window = `(ok + degraded) / checks`, summed over the days in the window, skipping days with no data. Degraded counts as up.
- Latency stats ignore probes with no response.
- **Recompute:** every probe run rebuilds **today's** entry from today's raw files, so today's bar is at most one run (an hour) behind and self-heals after a failed run. Past days are never rewritten.
- `daily.json` keeps every day (about 100 KB per year).

## 5. Health endpoints on nyc (plan step 5.3, not deployed yet)

The prober does not need these; once deployed they give better failure details.

**UWAPI `GET /universal/v1/health/`**: always HTTP 200, cached 30 s.

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-29T19:05:12Z",
  "cached": false,
  "components": {
    "solver-classic": { "status": "ok",   "latencyMs": 142,  "detail": "canary ok" },
    "solver-one":     { "status": "ok",   "latencyMs": 388,  "detail": "canary ok" },
    "solver-puzzles": { "status": "down", "latencyMs": 95,   "detail": "canary returned no data" },
    "solver-py":      { "status": "down", "latencyMs": null, "detail": "solver unreachable: ConnectionError" }
  }
}
```

- `status` is only `ok` or `down`; the prober decides `degraded` from `latencyMs`.
- Each backend: the solver's own `/health` must answer (any HTTP response), then the canary (same values as section 1) must match.
- `detail`: `canary ok`, `canary mismatch: expected <value>/<remoteness>, got <value>/<remoteness>`, `canary returned no data`, `canary returned no moves`, `solver unreachable: <ErrorClass>`, `timeout after 50000 ms`, `check failed: <ErrorClass>`, `canary <game>/<variant> is not registered`.

**Solver `GET /health`** (localhost only, called by UWAPI): liveness only, always HTTP 200.

```json
{ "status": "ok", "uptimeSeconds": 86400, "rssMb": 42.1, "timestamp": "2026-10-29T19:05:12Z" }
```

GamesmanClassic adds `"gameProcesses": 3` (game processes currently running; they start on demand and exit when idle).

## Who writes what

| File | Written by | When |
|---|---|---|
| `raw/.../HHMMSS.json` | prober | every run |
| `current.json` | prober | every run |
| `daily.json` (today's entry) | prober | every run |
| deletion of raw days older than 30 | cleanup job | daily |

The probe and cleanup workflows share one `concurrency` group, so they never push at the same time. Pushes retry with `git pull --rebase`.

## Decisions (all settled)

1. The prober decides `degraded` from its own thresholds.
2. When nyc is unreachable, every component is `down` with its real reason. No `unknown` state.
3. Bar color is the day's worst status. If that turns out too strict, switching to a downtime-percentage rule changes only the page's drawing, not the data.
4. Every probe run recomputes today's `daily.json` entry; the separate job only cleans up raw files.
5. Frontend markers, the instructions game and all four canaries are chosen and verified (section 1).
6. The prober runs **hourly** and confirms a bad check by re-checking it once, 90 s later, in the same run. This replaces the earlier rule of two consecutive scheduled runs (which at an hourly schedule would add an hour of delay).
7. *Later:* `recent.json` with the last 24 hours of per-probe latency (more useful with a finer schedule).
