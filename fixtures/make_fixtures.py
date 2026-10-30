"""Generate test data sets for the status pages (Uni /status and site/index.html).

Each set is a folder with current.json and daily.json in the format of docs/DATA_FORMATS.md.
Times are relative to now, so regenerate before testing (a "stale" set only means something
relative to the current time).

    python3 fixtures/make_fixtures.py          # writes fixtures/out/<set>/
    python3 fixtures/serve.py                  # serves them on http://localhost:8099/<set>

Sets: all-green, outage, degraded-day, gray-days, long-outage, stale, all-down, future-schema,
and flaky (serves once, then fails, to test "keep the last data"). Any other name, such as
/missing, returns 404 (fetch failure).
"""

import argparse
import copy
import datetime as dt
import json
import random
import shutil
from pathlib import Path

GROUPS = [
    {'id': 'website', 'name': 'Website', 'components': ['frontend']},
    {'id': 'api', 'name': 'API', 'components': ['api', 'instructions']},
    {'id': 'solvers', 'name': 'Solvers',
     'components': ['solver-classic', 'solver-one', 'solver-puzzles', 'solver-py']},
]
NAMES = {
    'frontend': 'GamesmanUni', 'api': 'Universal Web API', 'instructions': 'Game instructions',
    'solver-classic': 'GamesmanClassic', 'solver-one': 'GamesmanOne',
    'solver-puzzles': 'GamesmanPuzzles', 'solver-py': 'GamesmanPy',
}
GROUP_OF = {c: g['id'] for g in GROUPS for c in g['components']}
BASE_LATENCY = {
    'frontend': 500, 'api': 290, 'instructions': 300, 'solver-classic': 140,
    'solver-one': 150, 'solver-puzzles': 120, 'solver-py': 130,
}
DAYS = 90


def iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def day_key(t):
    return t.strftime('%Y-%m-%d')


def worst(counts):
    return 'down' if counts['down'] else 'degraded' if counts['degraded'] else 'ok'


def make_day(rng, cid, ok, degraded=0, down=0, slow_ms=None):
    checks = ok + degraded + down
    base = BASE_LATENCY[cid]
    answered = ok + degraded
    latencies = sorted(base * rng.uniform(0.8, 1.6) for _ in range(ok)) + [slow_ms or 35000] * degraded
    latencies.sort()
    day = {'checks': checks, 'ok': ok, 'degraded': degraded, 'down': down,
           'avgLatencyMs': round(sum(latencies) / answered) if answered else None,
           'p95LatencyMs': round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]) if answered else None,
           'worst': 'ok'}
    if checks:
        day['worst'] = worst(day)
    return day


def green_history(rng, now, days=DAYS):
    """Every component ok for `days` days; today has the runs so far (one per hour at :17)."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # At least one run today, so every set has a today entry to modify.
    runs_today = max(1, now.hour + (1 if now.minute >= 17 else 0))
    daily = {}
    for cid in NAMES:
        daily[cid] = {}
        for i in range(days - 1, -1, -1):
            d = today - dt.timedelta(days=i)
            daily[cid][day_key(d)] = make_day(rng, cid, ok=runs_today if i == 0 else 24)
    return daily


def component(cid, status='ok', since=None, checked=None, latency=None, detail='ok',
              last_failure=None, last_change=None):
    return {'name': NAMES[cid], 'group': GROUP_OF[cid], 'status': status, 'since': iso(since),
            'lastCheckedAt': iso(checked), 'latencyMs': latency, 'detail': detail,
            'lastFailure': last_failure, 'lastStateChange': last_change}


def make_current(now, components, updated=None):
    updated = updated or now
    order = ['ok', 'degraded', 'down']
    overall = max((c['status'] for c in components.values()), key=order.index)
    return {'schemaVersion': 1, 'updatedAt': iso(updated), 'overall': overall,
            'groups': GROUPS, 'components': components}


def all_ok_components(now, checked, since_days=DAYS):
    since = now - dt.timedelta(days=since_days)
    return {cid: component(cid, since=since, checked=checked, latency=BASE_LATENCY[cid])
            for cid in NAMES}


def last_run(now):
    t = now.replace(minute=17, second=12, microsecond=0)
    return t if t <= now else t - dt.timedelta(hours=1)


def build(now):
    rng = random.Random(7)
    checked = last_run(now)
    today = day_key(now)
    sets = {}

    # All green: 90 full days, everything ok.
    daily = green_history(rng, now)
    sets['all-green'] = (make_current(checked, all_ok_components(now, checked)), daily)

    # Outage: solver-py down for the last 3 runs; a blip on frontend 12 days ago.
    daily = green_history(rng, now)
    runs = daily['solver-py'][today]['checks']
    daily['solver-py'][today] = make_day(rng, 'solver-py', ok=max(0, runs - 3), down=min(3, runs))
    blip_day = day_key(now - dt.timedelta(days=12))
    daily['frontend'][blip_day] = make_day(rng, 'frontend', ok=23, down=1)
    comps = all_ok_components(now, checked)
    down_since = checked - dt.timedelta(hours=2)
    comps['solver-py'] = component(
        'solver-py', 'down', since=down_since, checked=checked, latency=None,
        detail='ReadTimeout after 60000 ms',
        last_failure={'at': iso(checked), 'detail': 'ReadTimeout after 60000 ms'},
        last_change={'at': iso(down_since), 'from': 'ok', 'to': 'down'})
    comps['frontend']['lastFailure'] = {'at': blip_day + 'T14:17:09Z', 'detail': 'HTTP 502'}
    comps['frontend']['since'] = blip_day + 'T15:17:09Z'
    comps['frontend']['lastStateChange'] = {'at': blip_day + 'T15:17:09Z', 'from': 'down', 'to': 'ok'}
    sets['outage'] = (make_current(checked, comps), daily)

    # Degraded: solver-one slow right now, a degraded day 10 days ago, and a blip that passed on retry.
    daily = green_history(rng, now)
    runs = daily['solver-one'][today]['checks']
    daily['solver-one'][today] = make_day(rng, 'solver-one', ok=max(0, runs - 1), degraded=min(1, runs), slow_ms=31412)
    slow_day = day_key(now - dt.timedelta(days=10))
    daily['solver-one'][slow_day] = make_day(rng, 'solver-one', ok=20, degraded=4, slow_ms=38000)
    comps = all_ok_components(now, checked)
    comps['solver-one'] = component(
        'solver-one', 'degraded', since=checked, checked=checked, latency=31412,
        detail='ok, slow (31412 ms > 30000 ms)',
        last_change={'at': iso(checked), 'from': 'ok', 'to': 'degraded'})
    blip = checked - dt.timedelta(days=2, hours=5)
    comps['solver-puzzles']['lastFailure'] = {'at': iso(blip), 'detail': 'HTTP 500'}
    sets['degraded-day'] = (make_current(checked, comps), daily)

    # Gray days: only 20 days of history, one missing day, one day with 0 checks, two short days.
    daily = green_history(rng, now, days=20)
    for cid in NAMES:
        daily[cid].pop(day_key(now - dt.timedelta(days=6)), None)
        daily[cid][day_key(now - dt.timedelta(days=9))] = make_day(rng, cid, ok=0)
        daily[cid][day_key(now - dt.timedelta(days=3))] = make_day(rng, cid, ok=6)
        daily[cid][day_key(now - dt.timedelta(days=14))] = make_day(rng, cid, ok=11)
    sets['gray-days'] = (make_current(checked, all_ok_components(now, checked, since_days=20)), daily)

    # Long outage: solver-classic down for 6 days (still down), and a 9-hour nyc-wide outage
    # 40 days ago that hit everything.
    daily = green_history(rng, now)
    for i in range(1, 6):
        daily['solver-classic'][day_key(now - dt.timedelta(days=i))] = make_day(rng, 'solver-classic', ok=0, down=24)
    start = now - dt.timedelta(days=6)
    daily['solver-classic'][day_key(start)] = make_day(rng, 'solver-classic', ok=10, down=14)
    daily['solver-classic'][today] = make_day(rng, 'solver-classic', ok=0, down=daily['solver-classic'][today]['checks'])
    nyc_day = day_key(now - dt.timedelta(days=40))
    for cid in NAMES:
        daily[cid][nyc_day] = make_day(rng, cid, ok=15, down=9)
    comps = all_ok_components(now, checked, since_days=40)
    since = start.replace(hour=10, minute=17, second=8, microsecond=0)
    comps['solver-classic'] = component(
        'solver-classic', 'down', since=since, checked=checked, latency=95,
        detail='canary mismatch: expected win/7, got nothing',
        last_failure={'at': iso(checked), 'detail': 'canary mismatch: expected win/7, got nothing'},
        last_change={'at': iso(since), 'from': 'ok', 'to': 'down'})
    for cid in NAMES:
        if cid != 'solver-classic':
            comps[cid]['lastFailure'] = {'at': nyc_day + 'T08:17:05Z', 'detail': 'ConnectTimeout'}
    sets['long-outage'] = (make_current(checked, comps), daily)

    # Stale: all green, but the last run finished 8 hours ago (the pages warn after 5).
    stale_at = checked - dt.timedelta(hours=8)
    daily = green_history(rng, stale_at)
    current = make_current(stale_at, all_ok_components(now, stale_at))
    daily_stale = {'updatedAt': iso(stale_at)}
    sets['stale'] = (current, daily, daily_stale)

    # All down: nyc unreachable for the last two runs.
    daily = green_history(rng, now)
    went_down = checked - dt.timedelta(hours=1)
    comps = {}
    for cid in NAMES:
        runs = daily[cid][today]['checks']
        daily[cid][today] = make_day(rng, cid, ok=max(0, runs - 2), down=min(2, runs))
        comps[cid] = component(
            cid, 'down', since=went_down, checked=checked, latency=None, detail='ConnectTimeout',
            last_failure={'at': iso(checked), 'detail': 'ConnectTimeout'},
            last_change={'at': iso(went_down), 'from': 'ok', 'to': 'down'})
    sets['all-down'] = (make_current(checked, comps), daily)

    # Future schema: a newer schemaVersion with an unknown field; the pages should warn and render.
    current, daily = copy.deepcopy(sets['all-green'][:2])
    current['schemaVersion'] = 2
    current['incidents'] = [{'title': 'something new'}]
    sets['future-schema'] = (current, daily, {'schemaVersion': 2})

    # Flaky: like outage, but serve.py answers only the first load, then fails.
    sets['flaky'] = copy.deepcopy(sets['outage'][:2])
    return sets


def write(out, now):
    if out.exists():
        shutil.rmtree(out)
    for name, value in build(now).items():
        current, components = value[0], value[1]
        extra = value[2] if len(value) > 2 else {}
        daily = {'schemaVersion': 1, 'updatedAt': current['updatedAt'], 'components': components}
        daily.update(extra)
        folder = out / name
        folder.mkdir(parents=True)
        (folder / 'current.json').write_text(json.dumps(current, indent=1) + '\n')
        (folder / 'daily.json').write_text(json.dumps(daily, indent=1) + '\n')
        print(f'{name}: overall {current["overall"]}, updated {current["updatedAt"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=Path(__file__).parent / 'out')
    parser.add_argument('--now', help='pretend it is this UTC time, e.g. 2026-11-02T12:30:00Z')
    args = parser.parse_args()
    now = (dt.datetime.strptime(args.now, '%Y-%m-%dT%H:%M:%SZ') if args.now
           else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0))
    write(args.out, now)


if __name__ == '__main__':
    main()
