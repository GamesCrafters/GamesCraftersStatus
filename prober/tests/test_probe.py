import copy
import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import pytest

import probe
from fake_server import (API, CANARY_PATHS, INSTRUCTIONS_PATH, FakeNyc, canary_body)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')
GOOD_INSTRUCTIONS = {'body': {'instructions': '# 0 to 10 by 1 or 2\n\n## Rules\n...'}}
SLOW = 0.25  # seconds; above the test degraded threshold (200 ms), below the timeout (400 ms)
STEPS = {
    'ok': GOOD_INSTRUCTIONS,
    'slow': {**GOOD_INSTRUCTIONS, 'delay': SLOW},
    'down': {'status': 500, 'body': 'oops'},
}
T0 = datetime(2026, 10, 29, 19, 5, 12, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start=T0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, **kwargs):
        self.t += timedelta(**kwargs)


@pytest.fixture
def fake():
    server = FakeNyc()
    server.set_healthy()
    yield server
    server.stop()


@pytest.fixture
def cfg(fake):
    config = probe.load_config(CONFIG_PATH)
    config['baseUrl'] = fake.base_url
    config['timeoutMs'] = 400
    config['connectTimeoutMs'] = 300
    config['defaultDegradedMs'] = 200
    config['retryDelaySeconds'] = 7
    return config


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def run(cfg, clock, sleeps, tmp_path):
    def _run(config=None):
        return probe.run_probe(config or cfg, str(tmp_path), now=clock.now, sleep=sleeps.append)
    return _run


def entry(result, cid):
    return result['record']['components'][cid]


def read(tmp_path, name):
    with open(tmp_path / name, encoding='utf-8') as handle:
        return json.load(handle)


# --- the config ---------------------------------------------------------------

def test_real_config_is_valid_and_has_the_agreed_canaries():
    config = probe.load_config(CONFIG_PATH)
    assert [c['id'] for c in config['components']] == [
        'frontend', 'api', 'instructions',
        'solver-classic', 'solver-one', 'solver-puzzles', 'solver-py']
    canaries = {c['id']: (c['game'], c['variant'], c['position'],
                          c['expect']['positionValue'], c['expect']['remoteness'])
                for c in config['components'] if c['check'] == 'canary'}
    assert canaries == {
        'solver-classic': ('0to10by1or2', 'regular', '1_0', 'win', 7),
        'solver-one': ('quixo', '3x3', '1_---------', 'win', 7),
        'solver-puzzles': ('sevenpennies', 'regular', '00000000_9', 'win', 14),
        'solver-py': ('stormyseas', 'beg1', 'oowowwwowowwwooowrwowpwwRwwoPwwwowo7', 'win', 3),
    }
    assert (config['defaultDegradedMs'], config['timeoutMs'], config['connectTimeoutMs'],
            config['retryDelaySeconds']) == (30000, 60000, 10000, 90)


@pytest.mark.parametrize('change, message', [
    (lambda c: c.pop('timeoutMs'), 'missing "timeoutMs"'),
    (lambda c: c.update(defaultDegradedMs=60000), 'less than timeoutMs'),
    (lambda c: c.update(connectTimeoutMs=70000), 'connectTimeoutMs must not be more'),
    (lambda c: c.pop('connectTimeoutMs'), 'missing "connectTimeoutMs"'),
    (lambda c: c['components'][0].update(group='nope'), 'unknown group'),
    (lambda c: c['components'][0].update(check='nope'), 'unknown check'),
    (lambda c: c['components'][1].update(id='frontend'), 'duplicate component id'),
    (lambda c: c['components'][3].pop('position'), 'missing "position"'),
    (lambda c: c['components'][3]['expect'].pop('remoteness'), 'missing "remoteness"'),
])
def test_config_validation(change, message):
    config = probe.load_config(CONFIG_PATH)
    change(config)
    with pytest.raises(probe.ConfigError, match=message):
        probe.validate_config(config)


# --- single checks ------------------------------------------------------------

def test_all_ok(run, fake, sleeps, tmp_path):
    result = run()
    for cid, got in result['record']['components'].items():
        assert got == {'status': 'ok', 'latencyMs': got['latencyMs'], 'httpStatus': 200, 'detail': 'ok'}, cid
        assert isinstance(got['latencyMs'], int)
    assert sleeps == []
    assert all(count == 1 for count in fake.hits.values())  # nothing was retried
    assert os.path.exists(tmp_path / 'raw' / '2026-10-29' / '190512.json')
    assert result['current']['overall'] == 'ok'


def test_canary_urls_are_encoded_and_correct(run, fake):
    run()
    expected = {
        'solver-classic': ('0to10by1or2/regular', '1_0'),
        'solver-one': ('quixo/3x3', '1_---------'),
        'solver-puzzles': ('sevenpennies/regular', '00000000_9'),
        'solver-py': ('stormyseas/beg1', 'oowowwwowowwwooowrwowpwwRwwoPwwwowo7'),
    }
    for game_variant, position in expected.values():
        queries = fake.queries[f'{API}/{game_variant}/positions/']
        assert parse_qs(queries[0]) == {'p': [position]}


def test_frontend_latency_is_the_sum_of_both_requests(run, fake):
    fake.set('/uni/', {'body': probe_html(), 'delay': 0.06})
    fake.set('/uni/assets/index.abc123.js', {'body': 'x', 'delay': 0.06})
    assert entry(run(), 'frontend')['latencyMs'] >= 115


def probe_html():
    from fake_server import HTML
    return HTML


def test_checks_run_one_at_a_time(run, fake):
    # In parallel the requests slow each other down and inflate the latency numbers.
    in_flight, overlap = [0], [False]
    original = fake._next_step

    def tracking(path):
        in_flight[0] += 1
        if in_flight[0] > 1:
            overlap[0] = True
        try:
            return original(path)
        finally:
            time.sleep(0.01)
            in_flight[0] -= 1
    fake._next_step = tracking
    run()
    assert overlap[0] is False


def test_connect_and_answer_limits_are_passed_separately():
    seen = {}

    class Response:
        status_code = 200

        def iter_content(self, size):
            yield b'{}'

        def close(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            seen.update(kwargs)
            return Response()

    probe.fetch(Session(), 'http://example.invalid/', 60000, 10000)
    assert seen['timeout'] == (10.0, 60.0)


def test_slow_answer_is_degraded(run, fake, sleeps):
    fake.set(INSTRUCTIONS_PATH, STEPS['slow'])
    got = entry(run(), 'instructions')
    assert got['status'] == 'degraded'
    assert got['detail'].startswith('ok, slow (') and got['detail'].endswith('> 200 ms)')
    assert got['httpStatus'] == 200


def test_component_can_override_the_degraded_threshold(cfg, fake, run):
    cfg['components'][2]['degradedMs'] = 100
    fake.set(INSTRUCTIONS_PATH, {**GOOD_INSTRUCTIONS, 'delay': 0.15})  # under 200, over 100
    assert entry(run(cfg), 'instructions')['status'] == 'degraded'


def test_wrong_canary_answer_is_down(run, fake):
    fake.set(CANARY_PATHS['solver-classic'][0], {'body': canary_body('lose', 3)})
    got = entry(run(), 'solver-classic')
    assert (got['status'], got['detail']) == ('down', 'canary mismatch: expected win/7, got lose/3')
    assert entry(run(), 'solver-one')['status'] == 'ok'


def test_canary_with_nothing_in_the_answer(run, fake):
    # What UWAPI returns when it swallows a backend error.
    fake.set(CANARY_PATHS['solver-puzzles'][0], {'body': {'error': 'Invalid Position'}})
    got = entry(run(), 'solver-puzzles')
    assert got['detail'] == 'canary mismatch: expected win/14, got nothing'


def test_canary_without_moves_is_down(run, fake):
    fake.set(CANARY_PATHS['solver-py'][0], {'body': canary_body('win', 3, moves=False)})
    assert entry(run(), 'solver-py')['detail'] == 'canary returned no moves'


def test_empty_instructions_are_down(run, fake):
    fake.set(INSTRUCTIONS_PATH, {'body': {'instructions': ''}})
    assert entry(run(), 'instructions')['detail'] == 'empty instructions'


def test_instructions_for_the_wrong_game_are_down(run, fake):
    fake.set(INSTRUCTIONS_PATH, {'body': {'instructions': '# Something else'}})
    assert entry(run(), 'instructions')['detail'] == 'unexpected instructions'


def test_http_error_is_down(run, fake):
    fake.set(CANARY_PATHS['solver-one'][0], {'status': 500, 'body': 'oops'})
    got = entry(run(), 'solver-one')
    assert (got['status'], got['detail'], got['httpStatus']) == ('down', 'HTTP 500', 500)
    assert isinstance(got['latencyMs'], int)


def test_timeout_is_down_with_no_latency(run, fake):
    fake.set(CANARY_PATHS['solver-py'][0], {'body': canary_body('win', 3), 'delay': 1.0})
    got = entry(run(), 'solver-py')
    assert (got['status'], got['detail']) == ('down', 'ReadTimeout after 400 ms')
    assert got['latencyMs'] is None and got['httpStatus'] is None


def test_connection_refused_is_down(cfg, run):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        dead_port = sock.getsockname()[1]
    cfg['baseUrl'] = f'http://127.0.0.1:{dead_port}'
    result = run(cfg)
    for cid in result['record']['components']:
        got = entry(result, cid)
        assert (got['status'], got['detail']) == ('down', 'ConnectionError'), cid
    assert result['current']['overall'] == 'down'


def test_invalid_json_is_down(run, fake):
    fake.set(f'{API}/', {'body': 'not json'})
    assert entry(run(), 'api')['detail'] == 'invalid JSON'


def test_unexpected_game_list_shape_is_down(run, fake):
    fake.set(f'{API}/', {'body': {'error': 'x'}})
    assert entry(run(), 'api')['detail'] == 'unexpected response'


def test_short_game_list_is_down(run, fake):
    fake.set(f'{API}/', {'body': [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]})
    assert entry(run(), 'api')['detail'] == 'only 3 games'


def test_frontend_marker_missing(run, fake):
    fake.set('/uni/', {'body': '<html><title>Not us</title></html>'})
    assert entry(run(), 'frontend')['detail'] == 'marker missing'


def test_frontend_without_a_script_reference(run, fake):
    fake.set('/uni/', {'body': '<title>GamesmanUni</title><div id="app"></div>'})
    assert entry(run(), 'frontend')['detail'] == 'asset not referenced'


def test_frontend_asset_missing(run, fake):
    fake.set('/uni/assets/index.abc123.js', {'status': 404, 'body': 'gone'})
    got = entry(run(), 'frontend')
    assert (got['status'], got['detail'], got['httpStatus']) == ('down', 'asset 404', 404)


def test_frontend_asset_empty(run, fake):
    fake.set('/uni/assets/index.abc123.js', {'body': ''})
    assert entry(run(), 'frontend')['detail'] == 'asset empty'


def test_a_bug_in_one_check_does_not_stop_the_run(run, monkeypatch):
    def boom(http, comp, degraded_ms):
        raise ValueError('bug')
    monkeypatch.setitem(probe.CHECKS, 'canary', boom)
    result = run()
    assert entry(result, 'solver-one')['detail'] == 'check failed: ValueError'
    assert entry(result, 'api')['status'] == 'ok'


def test_total_time_over_the_limit_is_a_timeout():
    class Slow:
        status_code = 200

        def iter_content(self, size):
            yield b'x'

        def close(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            time.sleep(0.05)
            return Slow()

    got = probe.fetch(Session(), 'http://example.invalid/', 10)
    assert got.error == 'timeout after 10 ms' and got.status is None


def test_detail_is_clipped():
    assert probe.clip('x' * 300) == 'x' * 197 + '...'
    assert probe.clip('short') == 'short'


# --- the retry rule -----------------------------------------------------------

@pytest.mark.parametrize('first, second, final, retried', [
    ('ok', 'ok', 'ok', False),
    ('down', 'ok', 'ok', True),
    ('slow', 'ok', 'ok', True),
    ('down', 'down', 'down', True),
    ('slow', 'slow', 'degraded', True),
    ('down', 'slow', 'degraded', True),
    ('slow', 'down', 'down', True),
])
def test_retry_outcomes(run, fake, sleeps, first, second, final, retried):
    fake.set(INSTRUCTIONS_PATH, STEPS[first], STEPS[second])
    got = entry(run(), 'instructions')
    assert got['status'] == final
    if retried:
        assert got['retried'] is True
        assert got['firstStatus'] == {'ok': 'ok', 'slow': 'degraded', 'down': 'down'}[first]
        assert sleeps == [7]
    else:
        assert 'retried' not in got and 'firstStatus' not in got
        assert sleeps == []


def test_only_components_that_were_not_ok_are_rechecked(run, fake, sleeps):
    fake.set(INSTRUCTIONS_PATH, STEPS['down'], STEPS['ok'])
    result = run()
    assert fake.hits[INSTRUCTIONS_PATH] == 2
    others = {p: n for p, n in fake.hits.items() if p != INSTRUCTIONS_PATH}
    assert others and all(n == 1 for n in others.values())
    assert [cid for cid, got in result['record']['components'].items() if got.get('retried')] == ['instructions']
    assert sleeps == [7]


def test_retry_happens_once_not_repeatedly(run, fake):
    fake.set(INSTRUCTIONS_PATH, STEPS['down'])
    run()
    assert fake.hits[INSTRUCTIONS_PATH] == 2


# --- current.json -------------------------------------------------------------

def test_overall_is_the_worst_status(run, fake):
    fake.set(INSTRUCTIONS_PATH, STEPS['slow'])
    assert run()['current']['overall'] == 'degraded'
    fake.set(CANARY_PATHS['solver-one'][0], {'status': 500})
    assert run()['current']['overall'] == 'down'


def test_groups_and_component_order_follow_the_config(run, cfg):
    current = run()['current']
    assert current['groups'] == [
        {'id': 'website', 'name': 'Website', 'components': ['frontend']},
        {'id': 'api', 'name': 'API', 'components': ['api', 'instructions']},
        {'id': 'solvers', 'name': 'Solvers',
         'components': ['solver-classic', 'solver-one', 'solver-puzzles', 'solver-py']},
    ]
    assert list(current['components']) == [c['id'] for c in cfg['components']]
    assert current['components']['solver-py']['name'] == 'GamesmanPy'
    assert current['components']['solver-py']['group'] == 'solvers'


def test_state_bookkeeping_across_runs(run, fake, clock):
    def api(result):
        return result['current']['components']['instructions']

    first = api(run())  # first ever run: no previous state
    assert first['status'] == 'ok' and first['since'] == '2026-10-29T19:05:12Z'
    assert first['lastStateChange'] is None and first['lastFailure'] is None

    clock.advance(hours=1)
    second = api(run())  # unchanged: since stays
    assert second['since'] == '2026-10-29T19:05:12Z'
    assert second['lastCheckedAt'] == '2026-10-29T20:05:12Z'

    clock.advance(hours=1)
    fake.set(INSTRUCTIONS_PATH, STEPS['down'])
    third = api(run())  # goes down
    assert third['status'] == 'down' and third['since'] == '2026-10-29T21:05:12Z'
    assert third['lastStateChange'] == {'at': '2026-10-29T21:05:12Z', 'from': 'ok', 'to': 'down'}
    assert third['lastFailure'] == {'at': '2026-10-29T21:05:12Z', 'detail': 'HTTP 500'}

    clock.advance(hours=1)
    fake.set(INSTRUCTIONS_PATH, STEPS['ok'])
    fourth = api(run())  # recovers; the failure is remembered
    assert fourth['status'] == 'ok' and fourth['since'] == '2026-10-29T22:05:12Z'
    assert fourth['lastStateChange'] == {'at': '2026-10-29T22:05:12Z', 'from': 'down', 'to': 'ok'}
    assert fourth['lastFailure'] == {'at': '2026-10-29T21:05:12Z', 'detail': 'HTTP 500'}

    clock.advance(hours=1)
    fake.set(INSTRUCTIONS_PATH, STEPS['down'], STEPS['ok'])
    fifth = api(run())  # a blip that passes on retry: still ok, but the blip is the last failure
    assert fifth['status'] == 'ok' and fifth['since'] == '2026-10-29T22:05:12Z'
    assert fifth['lastFailure'] == {'at': '2026-10-29T23:05:12Z', 'detail': 'HTTP 500'}
    assert fifth['lastStateChange'] == fourth['lastStateChange']


def test_unreadable_or_newer_state_is_treated_as_a_first_run(run, tmp_path):
    (tmp_path / 'current.json').write_text('{not json')
    assert run()['current']['components']['api']['lastStateChange'] is None
    (tmp_path / 'current.json').write_text(json.dumps({'schemaVersion': 99, 'components': {'api': {'status': 'down'}}}))
    assert run()['current']['components']['api']['lastStateChange'] is None


# --- raw files ----------------------------------------------------------------

def test_raw_file_matches_the_spec(run, fake, tmp_path):
    fake.set(INSTRUCTIONS_PATH, STEPS['down'], STEPS['ok'])
    run()
    record = read(tmp_path, 'raw/2026-10-29/190512.json')
    assert record['schemaVersion'] == 1 and record['startedAt'] == '2026-10-29T19:05:12Z'
    assert list(record['components']['instructions']) == [
        'status', 'latencyMs', 'httpStatus', 'detail', 'retried', 'firstStatus']
    assert list(record['components']['api']) == ['status', 'latencyMs', 'httpStatus', 'detail']
    assert record['components']['instructions']['firstStatus'] == 'down'


def test_nothing_from_a_response_body_is_stored_or_printed(run, fake, tmp_path, capsys):
    secret = 'SECRET-TOKEN-123'
    fake.set(f'{API}/', {'status': 500, 'body': secret})
    fake.set(INSTRUCTIONS_PATH, {'body': {'instructions': secret}})
    fake.set(CANARY_PATHS['solver-one'][0], {'body': {'positionValue': secret, 'remoteness': secret}})
    fake.set(CANARY_PATHS['solver-py'][0], {'body': {'positionValue': 'lose', 'remoteness': 4}})
    result = run()
    probe.main(['--config', CONFIG_PATH, '--data-dir', str(tmp_path / 'cli'),
                '--base-url', fake.base_url, '--retry-delay', '0'])
    printed = capsys.readouterr().out
    stored = ''.join(open(os.path.join(root, name), encoding='utf-8').read()
                     for root, _, names in os.walk(tmp_path) for name in names)
    assert secret not in stored and secret not in printed
    assert entry(result, 'solver-one')['detail'] == 'canary mismatch: expected win/7, got unexpected/unexpected'
    assert entry(result, 'solver-py')['detail'] == 'canary mismatch: expected win/3, got lose/4'  # normal values still shown


def test_safe_word():
    assert probe.safe_word('win') == 'win' and probe.safe_word(7) == '7'
    for bad in ['<script>', 'a' * 40, 'Win', '', None, True, 1.5, ['x'], {'a': 1}]:
        assert probe.safe_word(bad) == 'unexpected', bad


# --- daily.json ---------------------------------------------------------------

def test_p95_is_nearest_rank():
    assert probe.p95([]) is None
    assert probe.p95([5]) == 5
    assert probe.p95(list(range(1, 101))) == 95
    assert probe.p95(list(range(20, 0, -1))) == 19


def test_summarize_day():
    got = probe.summarize_day([('ok', 100), ('ok', 200), ('down', None), ('degraded', 300)])
    assert got == {'checks': 4, 'ok': 2, 'degraded': 1, 'down': 1,
                   'avgLatencyMs': 200, 'p95LatencyMs': 300, 'worst': 'down'}
    quiet = probe.summarize_day([('down', None)])
    assert (quiet['avgLatencyMs'], quiet['p95LatencyMs'], quiet['worst']) == (None, None, 'down')
    assert probe.summarize_day([('ok', 10), ('degraded', 20)])['worst'] == 'degraded'


def test_daily_counts_each_run_once_with_its_final_status(run, fake, clock, tmp_path):
    run()                                              # all ok
    clock.advance(hours=1)
    fake.set(INSTRUCTIONS_PATH, STEPS['down'], STEPS['ok'])
    run()                                              # blip, recorded ok
    clock.advance(hours=1)
    fake.set(INSTRUCTIONS_PATH, STEPS['down'])
    run()                                              # real failure
    day = read(tmp_path, 'daily.json')['components']['instructions']['2026-10-29']
    assert (day['checks'], day['ok'], day['degraded'], day['down'], day['worst']) == (3, 2, 0, 1, 'down')
    assert read(tmp_path, 'daily.json')['components']['api']['2026-10-29']['worst'] == 'ok'


def test_past_days_are_never_rewritten_and_gaps_stay_empty(run, clock, tmp_path):
    past = {'checks': 24, 'ok': 24, 'degraded': 0, 'down': 0,
            'avgLatencyMs': 1, 'p95LatencyMs': 2, 'worst': 'ok'}
    (tmp_path / 'daily.json').write_text(json.dumps({
        'schemaVersion': 1, 'updatedAt': 'x',
        'components': {'api': {'2026-10-27': past}}}))
    run()
    api = read(tmp_path, 'daily.json')['components']['api']
    assert list(api) == ['2026-10-27', '2026-10-29']      # 10-28 has no entry: gray, not down
    assert api['2026-10-27'] == past


def test_utc_day_rollover(run, clock, tmp_path):
    clock.t = datetime(2026, 10, 29, 23, 50, tzinfo=timezone.utc)
    run()
    clock.t = datetime(2026, 10, 30, 0, 10, tzinfo=timezone.utc)
    run()
    api = read(tmp_path, 'daily.json')['components']['api']
    assert sorted(api) == ['2026-10-29', '2026-10-30']
    assert api['2026-10-29']['checks'] == 1 and api['2026-10-30']['checks'] == 1
    assert sorted(os.listdir(tmp_path / 'raw')) == ['2026-10-29', '2026-10-30']


def test_todays_entry_heals_from_the_raw_files(run, clock, tmp_path):
    run()
    clock.advance(hours=1)
    run()
    daily = read(tmp_path, 'daily.json')
    daily['components']['api']['2026-10-29'] = {'checks': 99}
    (tmp_path / 'daily.json').write_text(json.dumps(daily))
    clock.advance(hours=1)
    run()
    assert read(tmp_path, 'daily.json')['components']['api']['2026-10-29']['checks'] == 3


def test_damaged_files_are_skipped(run, clock, tmp_path):
    run()
    (tmp_path / 'raw' / '2026-10-29' / 'garbage.json').write_text('{oops')
    (tmp_path / 'daily.json').write_text('{also broken')
    clock.advance(hours=1)
    run()
    assert read(tmp_path, 'daily.json')['components']['api']['2026-10-29']['checks'] == 2


# --- the command line ---------------------------------------------------------

def test_cli_prints_a_summary_and_exits_zero_even_when_something_is_down(fake, tmp_path, capsys):
    fake.set(CANARY_PATHS['solver-puzzles'][0], {'status': 500})
    code = probe.main(['--config', CONFIG_PATH, '--data-dir', str(tmp_path),
                       '--base-url', fake.base_url, '--retry-delay', '0'])
    out = capsys.readouterr().out
    assert code == 0
    assert 'overall: down' in out
    assert 'solver-puzzles' in out and 'HTTP 500' in out and '(retried, first: down)' in out
    assert 'frontend' in out


def test_cli_reports_a_bad_config(tmp_path, capsys):
    bad = tmp_path / 'bad.json'
    bad.write_text('{}')
    assert probe.main(['--config', str(bad), '--data-dir', str(tmp_path)]) == 2
    assert 'config error' in capsys.readouterr().err
