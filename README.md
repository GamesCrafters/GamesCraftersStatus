# GamesCraftersStatus
Status monitoring for GamesmanUni and its solver backends. The status page itself lives in GamesmanUni at `/uni/status`; this repo runs the checks and stores the results.

- `prober/`  probes the services hourly and records results
- `site/`    small fallback status page (GitHub Pages) for when nyc is down
- `fixtures/` test data sets for both status pages (`make_fixtures.py`, `serve.py`)
- `docs/`    plan, data-format spec and [health checks reference](docs/HEALTH.md)
- `.github/workflows/`  scheduled probe and cleanup jobs
- `data` branch: the results (`current.json`, `daily.json`, `raw/`)
