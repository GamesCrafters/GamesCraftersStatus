# Health checks: reference

How GamesCrafters decides whether GamesmanUni and its solvers work. This is the maintainer reference: what each piece does, how to change it, and how to debug it. Response and file formats are in [DATA_FORMATS.md](DATA_FORMATS.md); the rollout plan is in [PLAN.md](PLAN.md).

## The idea in one paragraph

A service is healthy when it **returns the right answer**, not when its process is alive. A solver can be running while its data is missing or corrupt, and UWAPI hides backend errors (a failed query becomes `{}`, then a normal-looking error reply), so an HTTP 200 proves nothing. Every check therefore asks a question whose answer we already know (a **canary**) and compares the reply. CPU and memory are not part of health: a busy solver that answers correctly is healthy, and slow is not broken.

## The three layers

```
 Prober (GitHub Actions, hourly, outside nyc)   "does it work for a user?"
    checks: website, API, instructions, 4 solver canaries (public URLs)
        │ HTTPS
        ▼
 UWAPI  GET /universal/v1/health/   (on nyc, cached 30 s)   "which solver is broken, and why?"
    per solver: liveness ping, then canary through the provider code
        │ localhost HTTP
        ▼
 Solver  GET /health   (Classic :8083, One :8084, Puzzles :9001, Py :9004)   "is the process up?"
    liveness only, always 200
```

| Layer | Lives in | Needs deploying to nyc? |
|---|---|---|
| Prober | GamesCraftersStatus `prober/` (not written yet) | No. It uses only public URLs, so it works without the layers below. |
| UWAPI `/health/` | GamesCraftersUWAPI `health.py` | Yes (optional; adds failure reasons) |
| Solver `/health` | each solver's server file | Yes (optional) |

Why a layer below the prober at all: from outside, a dead solver and a corrupt database look the same ("got nothing"). Only UWAPI, running on nyc, can tell "solver unreachable" from "canary returned no data".

## Statuses

| Status | Meaning |
|---|---|
| `ok` | correct answer in under 30 s |
| `degraded` | correct answer, but slower than 30 s (a person would be annoyed, but it works) |
| `down` | no answer, error, wrong answer, or no reply within 60 s (or no connection within 10 s) |

60 s is the same limit real requests get (`REQUEST_TIMEOUT_S` in UWAPI's `games/models.py`), so `down` means a real user would have failed too. Only the prober assigns `degraded`, from its own config, so the threshold can change without redeploying nyc. UWAPI's `/health/` reports only `ok` or `down`.

## The canaries

One known-answer query per solver, chosen as the smallest stable game on it. Verified against the live API on 2026-10-29.

| Component | Game / variant | Position | Expected value / remoteness |
|---|---|---|---|
| `solver-classic` | `0to10by1or2` / `regular` | `1_0` | win / 7 |
| `solver-one` | `quixo` / `3x3` | `1_---------` | win / 7 |
| `solver-puzzles` | `sevenpennies` / `regular` | `00000000_9` | win / 14 |
| `solver-py` | `stormyseas` / `beg1` | `oowowwwowowwwooowrwowpwwRwwoPwwwowo7` | win / 3 |

A canary passes when `positionValue` and `remoteness` match and `moves` is non-empty.

**The canaries live in two places** and must change together:
- UWAPI: `CANARIES` in `health.py`
- Prober: `prober/config.json` in this repo

**When to change them:** a canary game is removed or renamed in UWAPI, or deliberately re-solved so the answer changes. Until you update them, the check correctly reports `down` ("canary mismatch" or "canary <game>/<variant> is not registered"). To pick a new value, query the live API by hand and verify it makes sense; do not copy it blindly from the solver you are testing.

## What the prober does

Runs hourly (GitHub Actions). Checks seven components one at a time (in parallel they slow each other down and inflate the latency numbers): `frontend`, `api`, `instructions`, and the four solvers. If any component is not `ok`, it re-checks only those after 90 s, in the same run, and records the retry's result. So one bad check never reaches the page, and a real problem is confirmed within a single run. Full rules and the outcome table are in DATA_FORMATS.md (section 2).

## UWAPI `GET /health/`

Code: `health.py`; route in `server.py`. Tests: `tests/test_health.py`.

For each solver, in parallel:
1. **Liveness ping:** `GET <solver>/health`. Any HTTP response counts as alive; a refused connection or timeout is `down` ("solver unreachable: ConnectionError"). The canary is skipped.
2. **Canary:** `variant.position_data(position)`, the same code the public positions route uses.

Behavior:
- Always HTTP 200. It reports on other services, so a solver being down must not look like UWAPI being down. Read `status` per component.
- Response cached 30 s behind a lock: many callers cause one round of checks.
- Time limits: liveness ping 40 s, whole per-solver check 50 s, prober request 60 s. Each layer's limit is shorter than the one above, so a layer can report its own timeout before the one above gives up.
- Checks run on a long-lived thread pool (16 workers), not a `with` block, because leaving a `with` block waits for hung tasks, which would make one hung solver hang the endpoint.
- `/games/health/` (check every variant) was removed from the public API: ~700 backend requests per call on an unauthenticated URL, and its timeouts could not fire. Checking many games belongs in a private nightly job (PLAN.md 5.4).

`detail` strings: `canary ok`, `canary mismatch: expected win/7, got lose/3`, `canary returned no data`, `canary returned no moves`, `solver unreachable: <ErrorClass>`, `timeout after 50000 ms`, `check failed: <ErrorClass>`, `canary <game>/<variant> is not registered`.

## Solver `GET /health`

Liveness only, always HTTP 200, same shape in all four:

```json
{ "status": "ok", "uptimeSeconds": 86400, "rssMb": 42.1, "timestamp": "2026-10-29T19:05:12Z" }
```

GamesmanClassic also reports `gameProcesses` (game processes currently running; they start on demand and exit when idle) and accepts `/health/` and query strings. These endpoints were rewritten because the old ones reported `degraded` with HTTP 503 from the CPU and memory of the HTTP process only, which says nothing about whether solved data can be read.

Solver ports are reachable only from nyc itself.

## Troubleshooting

| You see | Likely cause | Check |
|---|---|---|
| `canary mismatch: expected win/7, got lose/3` | Solver answered wrongly, or the game was re-solved | Query the position by hand. If the new answer is right, update the canaries. |
| `canary returned no data` | Solver errored or its data is unreadable (UWAPI turned the error into `{}`) | Solver logs on nyc |
| `canary returned no moves` | Value is right but the move list is empty (partially broken data) | Query the position by hand |
| `solver unreachable: ConnectionError` | Solver process is down | `systemctl status` for that solver on nyc |
| `solver unreachable: ReadTimeout` or `timeout after 50000 ms` | Solver is hung or very overloaded | Solver logs, CPU, memory on nyc |
| `canary <game>/<variant> is not registered` | The canary game was removed or renamed in UWAPI | Update `CANARIES` and the prober config |
| Prober sees `ConnectTimeout` on everything | nyc is down, or the route from GitHub to nyc is broken | Try from another network |
| A day is gray | GitHub skipped or delayed runs. This is "no data", not an outage | Actions tab in this repo |

Manual checks from any machine:

```zsh
B=https://nyc.cs.berkeley.edu/universal/v1
curl -s "$B/0to10by1or2/regular/positions/?p=1_0"          # a canary; look for positionValue and remoteness
curl -s "$B/health/"                                        # UWAPI report (once deployed)
```

On nyc: `curl -s http://localhost:8083/health` (and 8084, 9001, 9004).

## Known limits

- One canary per solver catches "this solver cannot serve data", not "game X is broken while the canary game is fine". Per-game coverage is a later nightly job (PLAN.md 5.4).
- From outside, "nyc is down" and "the route from GitHub to nyc is broken" look the same.
- Hourly checks can miss a problem shorter than the gap between runs.
- The status page itself depends on nyc; the small fallback page on GitHub Pages is what works when nyc is down.
