import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import lessons
import paper_loop
import research_learning
import research_companion as rc


def outcome():
    return {'id': 'e1', 'ticker': 'TEST', 'outcome': 'helped', 'net_outcome': 'helped',
            'outcome_status': 'scored', 'intended_side': 'buy', 'confidence': 1.,
            'outcome_move_bps': 20., 'outcome_executable_move_bps': 5., 'verdict': 'PASS'}


def test_correction_replaces_vote_and_retains_prior_evidence(tmp_path):
    path = tmp_path/'lessons.json'
    event = outcome()
    lessons.record_outcome(path, event)
    assert lessons.record_outcome(path, event) is None
    event.update(net_outcome='hurt', outcome_executable_move_bps=-10.)
    corrected = lessons.record_outcome(path, event)
    rows = lessons.recent(path)
    assert len(rows) == 1 and rows[0]['net_outcome'] == 'hurt'
    assert corrected['revision'] == 2 and corrected['prior_versions'][0]['net_outcome'] == 'helped'
    assert lessons.record_outcome(path, event) is None
    record = lessons.track_record(path, ticker='TEST', verdict='PASS', lateness=None, scope={})
    assert record['side_stats']['buy']['hurt'] == 1
    assert '90-100' in record['confidence_bands']


@pytest.mark.parametrize('content', ['{broken', '{}', '[1]'])
def test_corrupt_lesson_memory_is_not_overwritten(tmp_path, content):
    path = tmp_path/'lessons.json'; path.write_text(content)
    with pytest.raises(ValueError, match='memory'):
        lessons.record_outcome(path, outcome())
    assert path.read_text() == content


def test_bad_numeric_evidence_does_not_replace_memory(tmp_path):
    path = tmp_path/'lessons.json'
    lessons.record_outcome(path, outcome())
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Non-finite'):
        lessons.record_outcome(path, dict(outcome(), confidence=float('nan')))
    assert path.read_bytes() == before


def test_disk_correction_invalidates_recall_cache(tmp_path):
    path = tmp_path/'lessons.json'
    lessons.record_outcome(path, outcome())
    rows = lessons.recent(path)
    rows[0]['text'] = 'Corrected externally with new evidence'
    path.write_text(json.dumps(rows))
    assert lessons.recent(path)[0]['text'].startswith('Corrected externally')


def test_corrupt_decision_ring_preserves_file_and_backup(tmp_path):
    path = tmp_path/'decisions.json'; path.write_text('{broken')
    ring = paper_loop.DecisionRing(path)
    with pytest.raises(ValueError, match='memory'):
        ring.append({'event': 'decision'})
    assert path.read_text() == '{broken'
    assert path.with_suffix('.json.corrupt.bak').read_text() == '{broken'


def test_expanded_memory_retains_newest_records(tmp_path):
    path = tmp_path/'decisions.json'
    events = [{'id': str(i), 'seq': 6000-i} for i in range(6000)]
    path.write_text(json.dumps({'events': events, 'seq': 6000}))
    service = object.__new__(rc.Companion)
    service.desk = SimpleNamespace(DECISIONS_PATH=path, _load_json=lambda p, d: json.loads(p.read_text()), _CORRUPT_PATHS=set())
    service._memory_cache = None
    selected = service.events()
    assert len(selected) == 5000 and selected[0]['id'] == '0' and selected[-1]['id'] == '4999'


def test_summary_reused_until_evidence_is_corrected(tmp_path, monkeypatch):
    path = tmp_path/'decisions.json'; path.write_text('{}')
    service = object.__new__(rc.Companion)
    service.desk = SimpleNamespace(DECISIONS_PATH=path, _CORRUPT_PATHS=set())
    service._memory_cache = None
    calls = []
    monkeypatch.setattr(service, 'events', lambda: calls.append(1) or [])
    first = service.memory_status()
    first['available'] = False
    assert service.memory_status()['available'] and len(calls) == 1
    path.write_text('{"events": []}')
    service.memory_status()
    assert len(calls) == 2


def test_latest_withdrawn_score_cannot_resurrect_old_training_sample():
    now = datetime.now(timezone.utc)
    event = dict(outcome(), ts=(now-timedelta(minutes=6)).isoformat(), outcome_ts=(now-timedelta(minutes=1)).isoformat(),
                 horizon_min=5, scoring_version='horizon-net-v2', llm_model='model', prompt_version='v1', input_hash='hash')
    assert research_learning.train([event], now)['qualified_samples'] == 1
    assert research_learning.train([dict(event, outcome_status='withdrawn'), event], now)['qualified_samples'] == 0
