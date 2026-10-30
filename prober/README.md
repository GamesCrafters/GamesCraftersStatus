# Prober

Checks GamesmanUni, its API and the four solvers through their public URLs and writes the results as JSON files. The rules and file formats are in [../docs/DATA_FORMATS.md](../docs/DATA_FORMATS.md); the design is in [../docs/HEALTH.md](../docs/HEALTH.md).

| File | What it is |
|---|---|
| `config.json` | Components, URLs, canaries and limits. The only place these live. |
| `probe.py` | One run: check everything, retry what was not ok, write `raw/`, `current.json`, `daily.json`. |
| `cleanup.py` | Deletes raw day folders older than 30 days. |
| `tests/` | Tests against a local fake server (no network needed). |

## Run it locally

```zsh
pip install -r requirements.txt
python probe.py --data-dir /tmp/status-data                 # against the real site
python probe.py --data-dir /tmp/status-data --retry-delay 0 # skip the 90 s wait before retries
python cleanup.py --data-dir /tmp/status-data
```

It prints one line per component and exits 0 even when something is down (a failing component is a result, not a prober error). It exits 2 if the config is invalid. Response bodies are never printed or stored.

## Test it

```zsh
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Changing a canary

Canaries are also in UWAPI's `health.py`; change both together, and verify the new value by hand against the live API first. See `docs/HEALTH.md`.
