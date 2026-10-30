import os
from datetime import date

import cleanup

TODAY = date(2026, 10, 29)


def make_day(tmp_path, name):
    folder = tmp_path / 'raw' / name
    folder.mkdir(parents=True)
    (folder / '120000.json').write_text('{}')
    return folder


def test_deletes_only_days_older_than_the_cutoff(tmp_path):
    for name in ['2026-09-27', '2026-09-28', '2026-09-29', '2026-10-28', '2026-10-29']:
        make_day(tmp_path, name)
    deleted = cleanup.cleanup(str(tmp_path), keep_days=30, today=TODAY)
    assert deleted == ['2026-09-27', '2026-09-28']          # cutoff is 2026-09-29, which is kept
    assert sorted(os.listdir(tmp_path / 'raw')) == ['2026-09-29', '2026-10-28', '2026-10-29']


def test_leaves_everything_else_alone(tmp_path):
    make_day(tmp_path, '2020-01-01')
    (tmp_path / 'raw' / 'notes').mkdir()
    (tmp_path / 'raw' / '2026-13-45').mkdir()               # looks like a date, is not one
    (tmp_path / 'raw' / '2020-01-02').write_text('a file, not a folder')
    (tmp_path / 'daily.json').write_text('{}')
    (tmp_path / 'current.json').write_text('{}')
    deleted = cleanup.cleanup(str(tmp_path), keep_days=30, today=TODAY)
    assert deleted == ['2020-01-01']
    assert sorted(os.listdir(tmp_path / 'raw')) == ['2020-01-02', '2026-13-45', 'notes']
    assert (tmp_path / 'daily.json').exists() and (tmp_path / 'current.json').exists()


def test_a_missing_raw_folder_is_fine(tmp_path):
    assert cleanup.cleanup(str(tmp_path), today=TODAY) == []


def test_keep_days_is_respected(tmp_path):
    for name in ['2026-10-20', '2026-10-27', '2026-10-28']:
        make_day(tmp_path, name)
    # 2026-10-27 is exactly 2 days old, which is kept; only older days go.
    assert cleanup.cleanup(str(tmp_path), keep_days=2, today=TODAY) == ['2026-10-20']


def test_cli(tmp_path, capsys):
    make_day(tmp_path, '2020-01-01')
    assert cleanup.main(['--data-dir', str(tmp_path), '--keep-days', '30']) == 0
    assert 'deleted 1 day folder(s): 2020-01-01' in capsys.readouterr().out
