# Data formats

This is the shared contract between the `/health` endpoint, the prober, the rollup step and the status page. Change it here first, then change the code.

**Status: DRAFT for review (step A1).** Items marked **[DECISION]** are open and listed again at the bottom. Example values (latencies, canary answers) are illustrative, not verified against the live servers.

## Conventions

- All timestamps are ISO 8601 in UTC with a trailing `Z`, e.g. `2026-09-18T19:05:12Z`.
- A "day" is a **UTC** calendar day, written `YYYY-MM-DD`. The page should label bars as UTC.
- Every file has `"schemaVersion": 1`. Bump it on any breaking change.
- Status values are exactly `ok`, `degraded`, `down` (lowercase).
- Detail strings are short (max about 200 characters), plain text, and already sanitized: no stack traces, tokens, headers, IPs or user data.

## Components

Ids are stable and never renamed. The page gets display names and grouping from `current.json`, so it needs no separate config.

| id | Group | What the prober checks |
|---|---|---|
| `frontend` | Website | `GET https://nyc.cs.berkeley.edu/uni/` returns 200 and looks like the app (contains a known marker) |
| `api` | API | `GET https://nyc.cs.berkeley.edu/universal/v1/` returns 200 and a non-empty game list; `/health` was reachable |
| `instructions` | API | `GET /universal/v1/<game>/<variant>/instructions/` for one known game returns non-empty instructions text (end to end: nyc reaching the Explainers repo on GitHub, the 2 s timeout, and the XML being valid; UWAPI returns an empty string on any failure, so 200 alone proves nothing) |
| `solver-classic` | Solvers | Reported by `/health` (canary on GamesmanClassic) |
| `solver-one` | Solvers | Reported by `/health` (canary on GamesmanOne) |
| `solver-puzzles` | Solvers | Reported by `/health` (canary on GamesmanPuzzles) |
| `solver-py` | Solvers | Reported by `/health` (canary on GamesmanPy) |

Groups, in display order: `website` (Website), `api` (API), `solvers` (Solvers).

Not monitored: the Lichess tablebase used for chess (a third-party service, not ours) is deliberately excluded.

## 1. `/health` response (served by UWAPI)

`GET /universal/v1/health`. Public, no secret. The result is cached in memory for 30 to 60 seconds. It reports only the four solvers. The prober measures the other components itself.

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-09-18T19:05:12Z",
  "cached": false,
  "components": {
    "solver-classic": { "status": "ok",   "latencyMs": 142, "detail": "canary ok" },
    "solver-one":     { "status": "ok",   "latencyMs": 388, "detail": "canary ok" },
    "solver-puzzles": { "status": "down", "latencyMs": 3000, "detail": "canary mismatch: expected win/5, got unsolved" },
    "solver-py":      { "status": "down", "latencyMs": 3000, "detail": "ConnectionError: connection refused" }
  }
}
```

Rules:

- `status` here is only `ok` or `down`. `down` means the solver did not answer, timed out, or the canary answer did not match. Whether a correct-but-slow answer counts as `degraded` is decided by the prober (see decision 1).
- Each backend has its own timeout, so one hung solver cannot hang the endpoint. A timeout is `down` with detail like `timeout after 3000 ms`.
- `cached` is true when the response was served from the cache. `generatedAt` is when the canaries actually ran.
- `detail` is `canary ok` on success. On failure it is `canary mismatch: expected <value>/<remoteness>, got <value>/<remoteness>` or `<ErrorClass>: <short message>`.

## 2. Raw run file: `raw/YYYY-MM-DD/HHMMSS.json` (on the `data` branch)

One file per probe run, named by the run's UTC start time. Kept about 30 days, then deleted by the cleanup job.

```json
{
  "schemaVersion": 1,
  "startedAt": "2026-09-18T19:05:12Z",
  "components": {
    "frontend":       { "observed": "ok",       "confirmed": "ok", "latencyMs": 210,  "httpStatus": 200, "detail": "ok" },
    "api":            { "observed": "ok",       "confirmed": "ok", "latencyMs": 305,  "httpStatus": 200, "detail": "ok" },
    "solver-classic": { "observed": "ok",       "confirmed": "ok", "latencyMs": 142,  "httpStatus": 200, "detail": "canary ok" },
    "solver-one":     { "observed": "degraded", "confirmed": "ok", "latencyMs": 3412, "httpStatus": 200, "detail": "canary ok, slow (threshold 3000 ms)" },
    "solver-puzzles": { "observed": "down",     "confirmed": "ok", "latencyMs": 3000, "httpStatus": 200, "detail": "canary mismatch: expected win/5, got unsolved" },
    "solver-py":      { "observed": "ok",       "confirmed": "ok", "latencyMs": 97,   "httpStatus": 200, "detail": "canary ok" },
    "instructions":   { "observed": "ok",       "confirmed": "ok", "latencyMs": 480,  "httpStatus": 200, "detail": "ok" }
  }
}
```

Fields per component:

- `observed`: what this single probe saw.
- `confirmed`: the state after the debounce rule (see below). This is what the page and uptime use.
- `latencyMs`: measured by the prober. `null` if there was no response.
- `httpStatus`: HTTP status of the probed URL, or `null` if there was no response.
- `detail`: sanitized text as described above.

**Debounce rule.** `confirmed` changes to a new status only after **2 consecutive probes** observe that same new status. After one probe that disagrees with `confirmed`, `confirmed` stays as it was and `current.json` records the pending change. (In the example above, `solver-puzzles` was observed `down` once, so it is still confirmed `ok`.)

**Degraded rule.** The prober sets `observed: "degraded"` when the check is correct but `latencyMs` is above that component's threshold (starting around 3000 ms, tuned from real data).

**If `/health` cannot be reached** (timeout, error, non-JSON), the four `solver-*` components are recorded `observed: "down"` with detail `unreachable via api`, and `api` is `down` with the real reason (see decision 2).

## 3. `current.json` (on the `data` branch, overwritten every run)

Latest confirmed state plus what the page needs to draw itself.

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-09-18T19:05:15Z",
  "overall": "ok",
  "groups": [
    { "id": "website",  "name": "Website",  "components": ["frontend"] },
    { "id": "api",      "name": "API",      "components": ["api", "instructions"] },
    { "id": "solvers",  "name": "Solvers",  "components": ["solver-classic", "solver-one", "solver-puzzles", "solver-py"] }
  ],
  "components": {
    "solver-puzzles": {
      "name": "GamesmanPuzzles",
      "group": "solvers",
      "status": "ok",
      "since": "2026-09-10T03:20:00Z",
      "lastCheckedAt": "2026-09-18T19:05:12Z",
      "latencyMs": 3000,
      "detail": "canary mismatch: expected win/5, got unsolved",
      "pending": { "status": "down", "count": 1, "firstSeen": "2026-09-18T19:05:12Z" },
      "lastFailure": { "at": "2026-09-17T08:15:00Z", "detail": "timeout after 3000 ms" }
    }
  }
}
```

(Only one component is shown; the real file has all seven.)

Fields:

- `overall`: the worst `status` across all components.
- `groups`: display order and membership. Names and groups live here so the page needs no config file.
- `status`: the confirmed status. `since`: when this confirmed status began.
- `pending`: `null` when nothing is pending, otherwise the status seen and how many consecutive probes have seen it. When `count` reaches 2, the status becomes confirmed and `pending` resets to `null`.
- `lastFailure`: the most recent probe that observed `down` (or `null`), kept across runs so the page can show "last failure reason".
- `detail` and `latencyMs`: from the most recent probe.

## 4. `daily.json` (on the `data` branch)

Per component, per UTC day, counting **confirmed** statuses.

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-09-18T19:05:15Z",
  "components": {
    "api": {
      "2026-09-17": { "checks": 288, "ok": 288, "degraded": 0, "down": 0, "avgLatencyMs": 301, "p95LatencyMs": 470, "worst": "ok" },
      "2026-09-18": { "checks": 229, "ok": 226, "degraded": 2, "down": 1, "avgLatencyMs": 320, "p95LatencyMs": 610, "worst": "down" }
    }
  }
}
```

Fields and rules:

- `checks` = `ok` + `degraded` + `down`. A full day is 288 checks at 5-minute spacing; fewer means GitHub skipped or delayed runs.
- **No data:** a day with no entry, or `checks: 0`, is drawn gray. Missed runs are never counted as `down`.
- `worst`: the worst confirmed status that day (`down` > `degraded` > `ok`). It sets the bar color (see decision 3).
- **Uptime %** over a window = `(ok + degraded) / (ok + degraded + down)`, summed over the days in the window, skipping days with no data. (Degraded counts as up.)
- Latency stats (`avgLatencyMs`, `p95LatencyMs`) ignore probes that had no response.
- The page's latency chart uses the daily `p95LatencyMs` over the last 90 days.
- `daily.json` keeps every day for now (about 100 KB per year). Revisit after a year.

## Who writes what

| File | Written by | When |
|---|---|---|
| `raw/.../HHMMSS.json` | prober | every run |
| `current.json` | prober | every run |
| `daily.json` | prober (recomputes **today's** entry from today's raw files) | every run (see decision 4) |
| deletion of old raw files | cleanup job | daily |

## Open decisions

1. **DECIDED: the prober decides `degraded`.** `/health` returns only `ok` or `down` plus `latencyMs`, and the prober applies each component's latency threshold from its own config. Thresholds live in one place and can be tuned without redeploying UWAPI to nyc.
2. **DECIDED: when `/health` is unreachable,** the four solvers are recorded `down` with detail `unreachable via api`, and `api` is `down` with the real reason. This matches what users experience and needs no extra status. (A separate gray `unknown` status was considered and rejected.)
3. **DECIDED: bar color is the day's worst confirmed status** (red if any confirmed `down` check, else yellow if any confirmed `degraded`, else green; gray for no data). A single confirmed outage makes the whole day red, like OpenAI's page. Revisit if it feels too strict; the stored daily counts mean switching to a downtime-percentage threshold only changes the page's drawing logic, not the data.
4. **DECIDED: every probe run recomputes today's entry in `daily.json`** from today's raw files (at most 288 small files), so today's bar is never more than about 5 minutes behind. The plan's `rollup.yml` becomes a simple daily **cleanup** job that only deletes raw files older than about 30 days. Notes for the workflows:
   - The probe workflow and the cleanup workflow must share the **same `concurrency` group name**, so they never push to the `data` branch at the same time. GitHub runs one job per group and keeps at most one waiting; a third arrival cancels the waiting one, which shows up as a gap (treated as "no data", never as `down`).
   - Pushes should use a `git pull --rebase` retry as a backup in case a push is rejected.
   - Recomputing from raw files makes today's entry self-healing: a failed or skipped run is corrected by the next one.
5. **Exact probe details** are still TBD: the marker text for `frontend`, and which game and variant `instructions` should request (pick a game that has instructions in the Explainers repo, verified against the live API once nyc is back).
6. **Optional later:** a `recent.json` with the last 24 hours of latency per component, for a finer latency chart than the daily p95.
