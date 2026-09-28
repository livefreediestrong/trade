from datetime import datetime, timezone
from types import SimpleNamespace
import json

import pytest
import desk_maintenance as maintenance
from test_upkeep_gateway import upkeep


@pytest.mark.parametrize('stamp,active,end', [
    ('2026-09-26T03:59:59+00:00', False, '02:00:00-04:00'),
    ('2026-09-26T04:00:00+00:00', True, '02:00:00-04:00'),
    ('2026-09-26T05:59:59+00:00', True, '02:00:00-04:00'),
    ('2026-09-26T06:00:00+00:00', False, '01:45:00-04:00'),
    ('2026-09-28T04:14:59+00:00', False, '01:45:00-04:00'),
    ('2026-09-28T04:15:00+00:00', True, '01:45:00-04:00'),
    ('2026-11-09T05:15:00+00:00', True, '01:45:00-05:00'),
    ('2026-11-01T06:30:00+00:00', True, '01:45:00-05:00'),
    ('2026-11-01T06:45:00+00:00', False, '01:45:00-05:00'),
])
def test_published_window_boundaries_and_eastern_timezone(stamp, active, end):
    result = maintenance.broker_window(datetime.fromisoformat(stamp))
    assert result['within_published_window'] is active
    assert result['ends_at'].endswith(end)
    assert 'not a live outage' in result['meaning']


def test_unknown_future_and_stale_receipts_are_not_recent():
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    for stamp in [None, 'bad', '2026-09-26T12:00:01Z', '2026-09-26T12:00:00', '2026-09-20T12:00:00Z']:
        assert not maintenance.recent(stamp, 1, now)
    assert maintenance.recent('2026-09-26T11:30:00Z', 1, now)
    with pytest.raises(ValueError):
        maintenance.broker_window(datetime(2026, 9, 26))


def test_plan_missing_records_remain_unknown_and_no_broker_calls(tmp_path, monkeypatch):
    import broker_ibkr
    monkeypatch.setattr(broker_ibkr, 'ensure_gateway', lambda **kw: pytest.fail('Read-only plan launched Gateway'))
    result = maintenance.snapshot(SimpleNamespace(DATA_DIR=tmp_path))
    assert result['watchdog']['recent'] is False
    assert result['daily']['problem_count'] is None
    assert result['backup']['ok'] is False


def health_payload(upkeep, **changes):
    return {'app_id': 'tomahawk-desk', 'port': upkeep.PORT, 'ok': True,
            'instance': {'source_root': str(upkeep.ROOT.resolve()), 'data_root': str(upkeep.DATA.resolve())},
            'code': {'started': 'version1', 'stale': False},
            'loop': {'rth_ok': False, 'decision_in_flight': False, 'pending_intents': 0}, **changes}


def test_upkeep_rejects_foreign_instances_and_data_roots(upkeep, monkeypatch, tmp_path):
    good = health_payload(upkeep)
    assert upkeep.matching_instance(good)
    for override in [{'app_id': 'other'}, {'port': upkeep.PORT+1}, {'instance': {}},
                     {'instance': {'source_root': str(tmp_path), 'data_root': str(upkeep.DATA)}},
                     {'instance': {'source_root': str(upkeep.ROOT), 'data_root': str(tmp_path)}}]:
        assert not upkeep.matching_instance({**good, **override})
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a): return json.dumps({**good, 'port': upkeep.PORT+1}).encode()
    monkeypatch.setattr(upkeep.urllib.request, 'urlopen', lambda *a, **kw: Reply())
    assert upkeep.health() is None


def test_upkeep_environment_matches_app_parser(upkeep, monkeypatch, tmp_path):
    monkeypatch.setattr(upkeep, 'ROOT', tmp_path)
    monkeypatch.delenv('UPKEEP_TEST_SETTING', raising=False)
    (tmp_path / '.env').write_text('UPKEEP_TEST_SETTING=old\nUPKEEP_TEST_SETTING="folder # with hash" # comment\n')
    assert upkeep._env('UPKEEP_TEST_SETTING', 'fallback') == 'folder # with hash'
    monkeypatch.setenv('UPKEEP_TEST_SETTING', 'environment')
    assert upkeep._env('UPKEEP_TEST_SETTING', 'fallback') == 'environment'


def prepare_daily(upkeep, monkeypatch, health):
    seen = []
    monkeypatch.setattr(upkeep, 'health', lambda **kw: health)
    monkeypatch.setattr(upkeep, 'verified_backup', lambda: seen.append('backup') or {'ok': True})
    for name in ('rotate_logs', 'sqlite_maintenance', 'data_usage'):
        monkeypatch.setattr(upkeep, name, lambda n=name: seen.append(n) or {})
    for name in ('archive_root_clutter', 'prune_archive', 'clean_caches'):
        monkeypatch.setattr(upkeep, name, lambda: pytest.fail('Unattended recursive cleanup'))
    monkeypatch.setattr(upkeep, 'update_tasks', lambda *a: None)
    monkeypatch.setattr(upkeep, 'log', lambda *a: None)
    monkeypatch.setattr(upkeep.code_version, 'fingerprint', lambda *a: 'version1')
    monkeypatch.setattr(upkeep.code_version, 'test_fingerprint', lambda *a: 'tests1')
    return seen


def test_backup_precedes_housekeeping_and_no_destructive_cleanup(upkeep, monkeypatch):
    seen = prepare_daily(upkeep, monkeypatch, health_payload(upkeep))
    upkeep.daily(with_tests=False)
    assert seen == ['backup', 'rotate_logs', 'sqlite_maintenance', 'data_usage']


def test_backup_failure_defers_housekeeping(upkeep, monkeypatch):
    seen = prepare_daily(upkeep, monkeypatch, health_payload(upkeep))
    def failed():
        seen.append('backup')
        raise OSError('disk full')
    monkeypatch.setattr(upkeep, 'verified_backup', failed)
    result = upkeep.daily(with_tests=False)
    assert seen == ['backup', 'data_usage']
    assert result['sqlite']['skipped'] and any('disk full' in p for p in result['problems'])


@pytest.mark.parametrize('health', [None, {'ok': True}, {'loop': {'rth_ok': True}},
    {'loop': {'rth_ok': False, 'decision_in_flight': True, 'pending_intents': 0}},
    {'loop': {'rth_ok': False, 'decision_in_flight': False, 'pending_intents': 1}}, {'code': {'stale': True}}])
def test_catchup_never_runs_heavy_tests_during_open_or_unknown_session(upkeep, monkeypatch, health):
    state = {**health_payload(upkeep), **health} if health else health
    # An otherwise healthy payload with no explicit session evidence is unknown.
    if health == {'ok': True}: state.pop('loop')
    prepare_daily(upkeep, monkeypatch, state)
    monkeypatch.setattr(upkeep, 'run_tests', lambda: pytest.fail('Heavy tests must wait'))
    assert 'deferred' in upkeep.daily()['tests']['skipped']


def test_changed_build_runs_once_and_failed_build_retries(upkeep, monkeypatch):
    prepare_daily(upkeep, monkeypatch, health_payload(upkeep))
    outcomes = iter([{'ok': False}, {'ok': True}])
    calls = []
    monkeypatch.setattr(upkeep, 'run_tests', lambda: calls.append(1) or next(outcomes))
    assert upkeep.daily()['tests']['ok'] is False
    assert upkeep.daily()['tests']['ok'] is True
    assert upkeep.daily()['tests']['skipped'] == 'This build already passed the upkeep test suite'
    assert len(calls) == 2


def test_changed_tests_rerun_even_when_app_version_passed(upkeep, monkeypatch):
    prepare_daily(upkeep, monkeypatch, health_payload(upkeep))
    upkeep._save('last_tests.json', {'ok': True, 'code': 'version1', 'test_code': 'older-tests'})
    calls = []
    monkeypatch.setattr(upkeep, 'run_tests', lambda: calls.append(1) or {'ok': True})
    assert upkeep.daily()['tests']['test_code'] == 'tests1'
    assert calls == [1]


def test_browser_update_reruns_tests_and_saves_full_current_version(upkeep, monkeypatch):
    state = health_payload(upkeep, code={'started': 'older', 'serving': 'version1',
                                        'stale': False, 'browser_files_changed': True})
    prepare_daily(upkeep, monkeypatch, state)
    upkeep._save('last_tests.json', {'ok': True, 'code': 'older', 'test_code': 'tests1'})
    calls = []
    monkeypatch.setattr(upkeep, 'run_tests', lambda: calls.append(1) or {'ok': True})
    result = upkeep.daily()['tests']
    assert result['ok'] is True and result['code'] == 'version1'
    assert upkeep.daily()['tests']['skipped'] == 'This build already passed the upkeep test suite'
    assert calls == [1]


@pytest.mark.parametrize('receipt,expected', [('older', 'older_passed'), ('current', 'passed')])
def test_maintenance_matches_browser_updates_to_current_full_receipt(tmp_path, monkeypatch, receipt, expected):
    monkeypatch.setattr(maintenance.code_version, 'status', lambda: {
        'started': 'older', 'serving': 'current', 'stale': False, 'browser_files_changed': True})
    monkeypatch.setattr(maintenance.code_version, 'test_fingerprint', lambda: 'tests1')
    root = tmp_path/'upkeep'; root.mkdir()
    (root/'last_tests.json').write_text(json.dumps({'ok': True, 'code': receipt,
        'test_code': 'tests1', 'at': '2026-09-26T12:00:00Z'}))
    result = maintenance.snapshot(SimpleNamespace(DATA_DIR=tmp_path), datetime(2026,9,26,13,tzinfo=timezone.utc))
    assert result['tests']['status'] == expected
    assert result['tests']['update_waiting'] is False


def test_files_changed_during_test_cannot_claim_current_pass(upkeep, monkeypatch):
    prepare_daily(upkeep, monkeypatch, health_payload(upkeep))
    monkeypatch.setattr(upkeep.code_version, 'fingerprint', lambda *a: 'version2')
    monkeypatch.setattr(upkeep, 'run_tests', lambda: {'ok': True, 'summary': '10 passed'})
    result = upkeep.daily()['tests']
    assert result['ok'] is False and result['changed_during_run'] is True


@pytest.mark.parametrize('old,ok,status', [(False,True,'passed'), (False,False,'failed'),
    (True,True,'older_passed'), (True,False,'older_failed')])
def test_maintenance_distinguishes_current_and_previous_results(tmp_path, monkeypatch, old, ok, status):
    monkeypatch.setattr(maintenance.code_version, 'status', lambda: {'started':'v2','stale':False})
    monkeypatch.setattr(maintenance.code_version, 'test_fingerprint', lambda: 'tests2')
    root = tmp_path/'upkeep'; root.mkdir()
    (root/'last_tests.json').write_text(json.dumps({'ok':ok, 'code':'v1' if old else 'v2',
        'test_code':'tests2', 'at':'2026-09-26T12:00:00Z','summary':'result', 'stdout':'PRIVATE DETAILS'}))
    (root/'last_daily.json').write_text(json.dumps({'tests':{'skipped':'deferred'}}))
    result = maintenance.snapshot(SimpleNamespace(DATA_DIR=tmp_path), datetime(2026,9,26,13,tzinfo=timezone.utc))
    assert result['tests']['status'] == status
    assert result['tests']['last_run_note'] == 'deferred'
    assert 'PRIVATE DETAILS' not in json.dumps(result)


@pytest.mark.parametrize('patch', [{'at':'2026-09-27T12:00:00Z'}, {'at':'2026-09-26T12:00:00'}, {'ok':'true'}])
def test_undated_future_or_malformed_results_cannot_pass(tmp_path, monkeypatch, patch):
    monkeypatch.setattr(maintenance.code_version, 'status', lambda: {'started':'v2','stale':False})
    monkeypatch.setattr(maintenance.code_version, 'test_fingerprint', lambda: 'tests2')
    root = tmp_path/'upkeep'; root.mkdir()
    (root/'last_tests.json').write_text(json.dumps({'ok':True, 'code':'v2','test_code':'tests2',
        'at':'2026-09-26T12:00:00Z', **patch}))
    result = maintenance.snapshot(SimpleNamespace(DATA_DIR=tmp_path), datetime(2026,9,26,13,tzinfo=timezone.utc))
    assert result['tests']['status'] == 'unknown'


def test_runner_saves_failure_details_and_partial_timeout_output(upkeep, monkeypatch):
    monkeypatch.setattr(upkeep, 'PYTHON', upkeep.UPKEEP/'python.exe'); upkeep.PYTHON.touch()
    def failed(cmd, **kwargs):
        assert '--tb=short' in cmd and '--tb=no' not in cmd
        assert not any(k in kwargs['env'] for k in ('TOMAHAWK_DATA_DIR','BROKER_PROVIDER','IBKR_LIVE','IBKR_ACCOUNT'))
        return SimpleNamespace(returncode=1, stdout='FAILED tests/test_example.py::test_case - AssertionError\n1 failed in 0.1s\n', stderr='diagnostic')
    monkeypatch.setattr(upkeep.subprocess, 'run', failed)
    result = upkeep.run_tests()
    assert result['ok'] is False and result['exit_code'] == 1
    assert 'AssertionError' in (upkeep.UPKEEP/result['details_file'].removeprefix('upkeep/')).read_text()
    def timeout(*a, **kw):
        raise upkeep.subprocess.TimeoutExpired('pytest', 900, output=b'partial progress', stderr=b'worker error')
    monkeypatch.setattr(upkeep.subprocess, 'run', timeout)
    timed = upkeep.run_tests()
    assert 'timed out' in timed['summary'] and timed['ok'] is False
    assert result['details_file'] != timed['details_file']
    assert 'partial progress' in (upkeep.UPKEEP/timed['details_file'].removeprefix('upkeep/')).read_text()


def test_test_fingerprint_includes_tests_runner_and_pins_but_not_data(tmp_path):
    import code_version
    (tmp_path/'tests').mkdir(); (tmp_path/'tools').mkdir(); (tmp_path/'data').mkdir()
    first = code_version.test_fingerprint(tmp_path)
    (tmp_path/'tests'/'test_a.py').write_text('assert True')
    second = code_version.test_fingerprint(tmp_path); assert second != first
    (tmp_path/'tools'/'desk_upkeep.py').write_text('runner')
    third = code_version.test_fingerprint(tmp_path); assert third != second
    (tmp_path/'requirements.txt').write_text('flask==3')
    fourth = code_version.test_fingerprint(tmp_path); assert fourth != third
    (tmp_path/'data'/'state.json').write_text('{}')
    assert code_version.test_fingerprint(tmp_path) == fourth


def test_maintenance_browser_script_states():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/test_maintenance_ui.cjs'], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
