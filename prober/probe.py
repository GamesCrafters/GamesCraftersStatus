#!/usr/bin/env python3
"""GamesCraftersStatus prober.

Checks GamesmanUni, its API and the four solvers through the public URLs, the
way a user reaches them, and records the results as JSON files. One run:

  1. Run every check once, one at a time (in parallel the requests slow each
     other down, which would inflate the latency numbers).
  2. Re-check only the components that were not ok, after retryDelaySeconds,
     and record the retry result (so one blip never reaches the page).
  3. Write raw/YYYY-MM-DD/HHMMSS.json, current.json and today's entry of
     daily.json under --data-dir.

The file formats and the rules are specified in docs/DATA_FORMATS.md. Response
bodies are never printed or stored: details are short fixed strings.

Usage:
  python probe.py --data-dir ./data [--config config.json] [--retry-delay 0]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

SCHEMA_VERSION = 1
STATUS_ORDER = {'ok': 0, 'degraded': 1, 'down': 2}
API_PREFIX = '/universal/v1'
MAX_DETAIL = 200
MAX_BODY_BYTES = 10 * 1024 * 1024
USER_AGENT = 'GamesCraftersStatusProber/1'
ASSET_RE = re.compile(r'src="(/[^"]*/assets/index[^"]*\.js)"')


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Time and small helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def safe_word(value) -> str:
    """A value from the server, made safe to put in a public detail string.

    Real position values are short lowercase words and remoteness is a whole
    number; anything else could be arbitrary text, so it is not copied.
    """
    if isinstance(value, bool):
        return 'unexpected'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r'[a-z]{1,12}', value):
        return value
    return 'unexpected'


def clip(text: str) -> str:
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 3] + '...'


def worst(statuses) -> str:
    return max(statuses, key=lambda s: STATUS_ORDER[s])


def p95(values):
    """Nearest-rank 95th percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


# ---------------------------------------------------------------------------
# One HTTP request
# ---------------------------------------------------------------------------

class Fetched:
    """Result of one request: either an error string, or a status and body."""
    __slots__ = ('status', 'body', 'error', 'elapsed_ms')

    def __init__(self, status=None, body=b'', error=None, elapsed_ms=None):
        self.status = status
        self.body = body
        self.error = error
        self.elapsed_ms = elapsed_ms


def fetch(session, url: str, timeout_ms: int, connect_timeout_ms: int | None = None) -> Fetched:
    """GET url. Errors become a short class name; the body is read in full (capped).

    connect_timeout_ms limits opening the connection; timeout_ms limits the answer.
    """
    started = time.monotonic()
    try:
        connect_s = (connect_timeout_ms or timeout_ms) / 1000
        response = session.get(url, timeout=(connect_s, timeout_ms / 1000), stream=True)
        try:
            body = b''
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    break
        finally:
            response.close()
    except requests.exceptions.ConnectTimeout:
        return Fetched(error='ConnectTimeout')
    except requests.exceptions.ReadTimeout:
        return Fetched(error=f'ReadTimeout after {timeout_ms} ms')
    except requests.exceptions.RequestException as err:
        return Fetched(error=type(err).__name__)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if elapsed_ms > timeout_ms:
        # requests' timeout is per socket read, so a slow trickle can exceed it.
        return Fetched(error=f'timeout after {timeout_ms} ms')
    return Fetched(response.status_code, body, None, elapsed_ms)


def fetch_json(http, path: str):
    """Return (fetched, data, failure). failure is an observation or None."""
    got = fetch(http.session, http.base + path, http.timeout_ms, http.connect_timeout_ms)
    if got.error:
        return None, None, down(got.error)
    if got.status != 200:
        return got, None, down(f'HTTP {got.status}', got.elapsed_ms, got.status)
    try:
        return got, json.loads(got.body), None
    except ValueError:
        return got, None, down('invalid JSON', got.elapsed_ms, got.status)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

def observation(status, latency_ms, http_status, detail):
    return {'status': status, 'latencyMs': latency_ms, 'httpStatus': http_status,
            'detail': clip(detail)}


def down(detail, latency_ms=None, http_status=None):
    return observation('down', latency_ms, http_status, detail)


def passed(latency_ms, http_status, degraded_ms):
    if latency_ms > degraded_ms:
        return observation('degraded', latency_ms, http_status,
                           f'ok, slow ({latency_ms} ms > {degraded_ms} ms)')
    return observation('ok', latency_ms, http_status, 'ok')


# ---------------------------------------------------------------------------
# The four kinds of check
# ---------------------------------------------------------------------------

def check_frontend(http, comp, degraded_ms):
    page = fetch(http.session, http.base + comp['path'], http.timeout_ms, http.connect_timeout_ms)
    if page.error:
        return down(page.error)
    if page.status != 200:
        return down(f'HTTP {page.status}', page.elapsed_ms, page.status)
    text = page.body.decode('utf-8', 'replace')
    if any(marker not in text for marker in comp['markers']):
        return down('marker missing', page.elapsed_ms, page.status)
    match = ASSET_RE.search(text)
    if not match:
        return down('asset not referenced', page.elapsed_ms, page.status)
    asset = fetch(http.session, http.base + match.group(1), http.timeout_ms, http.connect_timeout_ms)
    if asset.error:
        return down(asset.error)
    total = page.elapsed_ms + asset.elapsed_ms
    if asset.status != 200:
        return down(f'asset {asset.status}', total, asset.status)
    if not asset.body:
        return down('asset empty', total, asset.status)
    return passed(total, asset.status, degraded_ms)


def check_game_list(http, comp, degraded_ms):
    got, data, failure = fetch_json(http, comp['path'])
    if failure:
        return failure
    if not isinstance(data, list):
        return down('unexpected response', got.elapsed_ms, got.status)
    if len(data) < comp['minGames']:
        return down(f'only {len(data)} games', got.elapsed_ms, got.status)
    return passed(got.elapsed_ms, got.status, degraded_ms)


def check_instructions(http, comp, degraded_ms):
    got, data, failure = fetch_json(http, comp['path'])
    if failure:
        return failure
    text = data.get('instructions') if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return down('empty instructions', got.elapsed_ms, got.status)
    if not text.startswith(comp['startsWith']):
        return down('unexpected instructions', got.elapsed_ms, got.status)
    return passed(got.elapsed_ms, got.status, degraded_ms)


def check_canary(http, comp, degraded_ms):
    path = (f"{API_PREFIX}/{comp['game']}/{comp['variant']}/positions/"
            f"?p={quote(comp['position'], safe='')}")
    got, data, failure = fetch_json(http, path)
    if failure:
        return failure
    want_value = comp['expect']['positionValue']
    want_remoteness = comp['expect']['remoteness']
    value = data.get('positionValue') if isinstance(data, dict) else None
    remoteness = data.get('remoteness') if isinstance(data, dict) else None
    if value is None or remoteness is None:
        return down(f'canary mismatch: expected {want_value}/{want_remoteness}, got nothing',
                    got.elapsed_ms, got.status)
    if (value, remoteness) != (want_value, want_remoteness):
        return down(f'canary mismatch: expected {want_value}/{want_remoteness}, '
                    f'got {safe_word(value)}/{safe_word(remoteness)}', got.elapsed_ms, got.status)
    moves = data.get('moves')
    if not isinstance(moves, list) or not moves:
        return down('canary returned no moves', got.elapsed_ms, got.status)
    return passed(got.elapsed_ms, got.status, degraded_ms)


CHECKS = {
    'frontend': check_frontend,
    'gameList': check_game_list,
    'instructions': check_instructions,
    'canary': check_canary,
}

REQUIRED_BY_CHECK = {
    'frontend': ('path', 'markers'),
    'gameList': ('path', 'minGames'),
    'instructions': ('path', 'startsWith'),
    'canary': ('game', 'variant', 'position', 'expect'),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def validate_config(cfg: dict) -> None:
    for key in ('baseUrl', 'timeoutMs', 'connectTimeoutMs', 'defaultDegradedMs',
                'retryDelaySeconds', 'groups', 'components'):
        if key not in cfg:
            raise ConfigError(f'config is missing "{key}"')
    if cfg['defaultDegradedMs'] >= cfg['timeoutMs']:
        raise ConfigError('defaultDegradedMs must be less than timeoutMs')
    if cfg['connectTimeoutMs'] > cfg['timeoutMs']:
        raise ConfigError('connectTimeoutMs must not be more than timeoutMs')
    group_ids = {group['id'] for group in cfg['groups']}
    seen = set()
    for comp in cfg['components']:
        for key in ('id', 'group', 'name', 'check'):
            if key not in comp:
                raise ConfigError(f'component {comp.get("id", "?")} is missing "{key}"')
        if comp['id'] in seen:
            raise ConfigError(f'duplicate component id "{comp["id"]}"')
        seen.add(comp['id'])
        if comp['group'] not in group_ids:
            raise ConfigError(f'component "{comp["id"]}" has unknown group "{comp["group"]}"')
        if comp['check'] not in CHECKS:
            raise ConfigError(f'component "{comp["id"]}" has unknown check "{comp["check"]}"')
        for key in REQUIRED_BY_CHECK[comp['check']]:
            if key not in comp:
                raise ConfigError(f'component "{comp["id"]}" is missing "{key}"')
        if comp['check'] == 'canary':
            for key in ('positionValue', 'remoteness'):
                if key not in comp['expect']:
                    raise ConfigError(f'component "{comp["id"]}" expect is missing "{key}"')


def load_config(path: str) -> dict:
    with open(path, encoding='utf-8') as handle:
        cfg = json.load(handle)
    validate_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Running the checks
# ---------------------------------------------------------------------------

class Http:
    """What one check needs to talk to the site: a session, the base URL and the limit."""
    def __init__(self, session, base, timeout_ms, connect_timeout_ms):
        self.session = session
        self.base = base
        self.timeout_ms = timeout_ms
        self.connect_timeout_ms = connect_timeout_ms


class Context:
    def __init__(self, cfg, now, make_session):
        self.base = cfg['baseUrl'].rstrip('/')
        self.timeout_ms = cfg['timeoutMs']
        self.connect_timeout_ms = cfg['connectTimeoutMs']
        self.default_degraded_ms = cfg['defaultDegradedMs']
        self.now = now
        self.make_session = make_session


def default_session():
    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    return session


def run_checks(ctx, comps):
    """Run the checks for comps one at a time. Returns {id: observation + 'checkedAt'}."""
    results = {}
    for comp in comps:
        degraded_ms = comp.get('degradedMs', ctx.default_degraded_ms)
        http = Http(ctx.make_session(), ctx.base, ctx.timeout_ms, ctx.connect_timeout_ms)
        try:
            result = CHECKS[comp['check']](http, comp, degraded_ms)
        except Exception as err:  # a bug in a check must not take the whole run down
            result = down(f'check failed: {type(err).__name__}')
        finally:
            http.session.close()
        result['checkedAt'] = ctx.now()
        results[comp['id']] = result
    return results


def run_with_retry(ctx, cfg, sleep):
    """Run all checks; re-check the non-ok ones once. Returns (final, first)."""
    comps = cfg['components']
    first = run_checks(ctx, comps)
    final = dict(first)
    again = [comp for comp in comps if first[comp['id']]['status'] != 'ok']
    if again:
        sleep(cfg['retryDelaySeconds'])
        second = run_checks(ctx, again)
        for comp in again:
            result = second[comp['id']]
            result['retried'] = True
            result['firstStatus'] = first[comp['id']]['status']
            final[comp['id']] = result
    return final, first


# ---------------------------------------------------------------------------
# Reading and writing the data files
# ---------------------------------------------------------------------------

def read_json(path):
    """Return the parsed file, or None if it is missing, unreadable or a newer schema."""
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('schemaVersion') != SCHEMA_VERSION:
        return None
    return data


def write_json(path, data) -> None:
    """Write atomically so a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def raw_record(cfg, final, started_at):
    components = {}
    for comp in cfg['components']:
        result = final[comp['id']]
        entry = {'status': result['status'], 'latencyMs': result['latencyMs'],
                 'httpStatus': result['httpStatus'], 'detail': result['detail']}
        if result.get('retried'):
            entry['retried'] = True
            entry['firstStatus'] = result['firstStatus']
        components[comp['id']] = entry
    return {'schemaVersion': SCHEMA_VERSION, 'startedAt': iso(started_at),
            'components': components}


def build_current(cfg, previous, final, first, started_at, updated_at):
    before = (previous or {}).get('components', {})
    components = {}
    for comp in cfg['components']:
        cid = comp['id']
        result, early, old = final[cid], first[cid], before.get(cid)
        status = result['status']

        if old and old.get('status') == status:
            since = old.get('since', iso(started_at))
            last_change = old.get('lastStateChange')
        else:
            since = iso(started_at)
            last_change = ({'at': since, 'from': old.get('status'), 'to': status}
                           if old else None)

        if status == 'down':
            last_failure = {'at': iso(result['checkedAt']), 'detail': result['detail']}
        elif early['status'] == 'down':
            last_failure = {'at': iso(early['checkedAt']), 'detail': early['detail']}
        else:
            last_failure = old.get('lastFailure') if old else None

        components[cid] = {
            'name': comp['name'],
            'group': comp['group'],
            'status': status,
            'since': since,
            'lastCheckedAt': iso(result['checkedAt']),
            'latencyMs': result['latencyMs'],
            'detail': result['detail'],
            'lastFailure': last_failure,
            'lastStateChange': last_change,
        }

    groups = [{'id': group['id'], 'name': group['name'],
               'components': [c['id'] for c in cfg['components'] if c['group'] == group['id']]}
              for group in cfg['groups']]
    return {'schemaVersion': SCHEMA_VERSION, 'updatedAt': iso(updated_at),
            'overall': worst(c['status'] for c in components.values()),
            'groups': groups, 'components': components}


def summarize_day(rows):
    """rows: list of (status, latencyMs or None), one per run that day."""
    counts = {status: 0 for status in STATUS_ORDER}
    latencies = []
    for status, latency in rows:
        if status in counts:
            counts[status] += 1
            if isinstance(latency, (int, float)):
                latencies.append(latency)
    checks = sum(counts.values())
    return {
        'checks': checks, 'ok': counts['ok'], 'degraded': counts['degraded'],
        'down': counts['down'],
        'avgLatencyMs': round(sum(latencies) / len(latencies)) if latencies else None,
        'p95LatencyMs': p95(latencies),
        'worst': worst([s for s in counts if counts[s]] or ['ok']),
    }


def recompute_today(data_dir, day, updated_at):
    """Rebuild one day's entry of daily.json from that day's raw files.

    Other days are left exactly as they are, so a past day is never rewritten.
    """
    path = os.path.join(data_dir, 'daily.json')
    existing = read_json(path) or {'schemaVersion': SCHEMA_VERSION, 'components': {}}
    components = existing.setdefault('components', {})

    rows = {}
    for raw_path in sorted(glob.glob(os.path.join(data_dir, 'raw', day, '*.json'))):
        record = read_json(raw_path)
        if not record:
            continue
        for cid, entry in record.get('components', {}).items():
            rows.setdefault(cid, []).append((entry.get('status'), entry.get('latencyMs')))

    for cid, cid_rows in rows.items():
        days = components.setdefault(cid, {})
        days[day] = summarize_day(cid_rows)
        components[cid] = dict(sorted(days.items()))

    existing['schemaVersion'] = SCHEMA_VERSION
    existing['updatedAt'] = iso(updated_at)
    write_json(path, existing)
    return existing


# ---------------------------------------------------------------------------
# One full run
# ---------------------------------------------------------------------------

def run_probe(cfg, data_dir, *, now=utc_now, sleep=time.sleep, make_session=default_session):
    validate_config(cfg)
    started_at = now()
    ctx = Context(cfg, now, make_session)
    final, first = run_with_retry(ctx, cfg, sleep)

    record = raw_record(cfg, final, started_at)
    raw_path = os.path.join(data_dir, 'raw', started_at.strftime('%Y-%m-%d'),
                            started_at.strftime('%H%M%S') + '.json')
    write_json(raw_path, record)

    current_path = os.path.join(data_dir, 'current.json')
    current = build_current(cfg, read_json(current_path), final, first, started_at, now())
    write_json(current_path, current)

    recompute_today(data_dir, started_at.strftime('%Y-%m-%d'), now())
    return {'record': record, 'current': current, 'final': final, 'raw_path': raw_path}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description='GamesCraftersStatus prober')
    parser.add_argument('--config', default=os.path.join(here, 'config.json'))
    parser.add_argument('--data-dir', required=True, help='folder to write raw/, current.json, daily.json')
    parser.add_argument('--base-url', help='override baseUrl from the config')
    parser.add_argument('--retry-delay', type=float, help='override retryDelaySeconds')
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as err:
        print(f'config error: {err}', file=sys.stderr)
        return 2
    if args.base_url:
        cfg['baseUrl'] = args.base_url
    if args.retry_delay is not None:
        cfg['retryDelaySeconds'] = args.retry_delay

    result = run_probe(cfg, args.data_dir)
    print(f"{result['record']['startedAt']}  {len(cfg['components'])} components")
    for comp in cfg['components']:
        entry = result['record']['components'][comp['id']]
        latency = '-' if entry['latencyMs'] is None else f"{entry['latencyMs']} ms"
        note = f"  (retried, first: {entry['firstStatus']})" if entry.get('retried') else ''
        print(f"  {comp['id']:15} {entry['status']:9} {latency:>9}  {entry['detail']}{note}")
    print(f"overall: {result['current']['overall']}   wrote {result['raw_path']}")
    return 0  # a failing component is a result, not an error in the prober


if __name__ == '__main__':
    sys.exit(main())
