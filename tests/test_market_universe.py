from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
import app as desk
import market_universe as universe

NOW=datetime(2026,9,23,18,tzinfo=timezone.utc)
HEADER='Nasdaq Traded|Symbol|Security Name|Listing Exchange|ETF|Test Issue|NextShares\n'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(desk,'DATA_DIR',tmp_path)
    monkeypatch.setattr(desk,'_CORRUPT_PATHS',set())
    return tmp_path/'market_universe.json'


def test_directory_includes_etfs_adrs_class_shares_and_excludes_unsupported():
    text=HEADER+'\n'.join([
        'Y|AAA|Ordinary shares|N|N|N|N','Y|ETF|Short Treasury Notes ETF|P|Y|N|N',
        'Y|ADR|American Depositary Shares|Q|N|N|N','Y|BRK.B|Class B stock|N|N|N|N',
        'Y|TEST|Test stock|N|N|Y|N','Y|PREF|Preferred stock|N|N|N|N',
        'Y|RIGHT|Rights|N|N|N|N','Y|WRT|Warrants|N|N|N|N','Y|UNT|Units|N|N|N|N',
        'N|DELIST|Removed|N|N|N|N','Y|NS|Next shares|N|N|N|Y',
        'File Creation Time:0923202618:00||||||'])
    rows=universe.parse(text)
    assert [r['symbol'] for r in rows]==['AAA','ADR','BRK.B','ETF']
    assert rows[-1]['etf'] is True


def test_incomplete_refresh_preserves_cache_and_cools_down(isolated,monkeypatch):
    desk._save_json(isolated,{'symbols':[{'symbol':'BBB'}],'updated_at':None})
    calls=[]
    def get(*a,**k):
        calls.append(1)
        return NS(raise_for_status=lambda:None,content=b'partial',text=HEADER+'Y|ONE|Stock|N|N|N|N')
    monkeypatch.setattr(universe.requests,'get',get)
    universe.refresh(desk)
    state=universe.load(desk)
    assert state['symbols']==[{'symbol':'BBB'}] and 'Incomplete' in state['error']
    after=datetime.fromisoformat(state['last_attempt_at'])+timedelta(seconds=30)
    p={'universe':'broad_us','symbols':['AAA']}
    assert universe.candidates(desk,p,0,after)==['AAA','BBB'] and len(calls)==1


def test_rotation_and_focus_are_separate(isolated,monkeypatch):
    desk._save_json(isolated,{'symbols':[{'symbol':s} for s in ['AAA','CCC','DDD']], 'updated_at':NOW.isoformat()})
    monkeypatch.setattr(universe,'refresh',lambda *a:pytest.fail('Unexpected refresh'))
    p={'universe':'broad_us','symbols':['AAA','BBB']}
    assert universe.candidates(desk,p,0,NOW)==['AAA','CCC']
    assert universe.candidates(desk,p,2,NOW)==['BBB','DDD']
    assert universe.candidates(desk,p,4,NOW)==['AAA','CCC']
    assert universe.candidates(desk,dict(p,universe='focus'),0,NOW)==['AAA','BBB']
    assert p['symbols']==['AAA','BBB']


def test_future_directory_timestamp_does_not_stay_fresh(isolated,monkeypatch):
    desk._save_json(isolated,{'symbols':[],'updated_at':(NOW+timedelta(days=1)).isoformat()})
    calls=[]
    monkeypatch.setattr(universe,'refresh',lambda *a:calls.append(1))
    universe.candidates(desk,{'universe':'broad_us','symbols':['AAA']},0,NOW)
    assert calls==[1]


def test_shared_candidates_keep_focus_budget_and_directory_exploration(isolated,monkeypatch):
    import market_discovery
    desk._save_json(isolated,{'symbols':[{'symbol':s} for s in ['AAA','CCC','DDD']], 'updated_at':NOW.isoformat()})
    monkeypatch.setattr(market_discovery,'symbols',lambda *a,**k:['RANKED'])
    p={'universe':'broad_us','symbols':['AAA','BBB'],'candidates_per_cycle':3}
    assert universe.candidates(desk,p,0,NOW)==['AAA','CCC','RANKED']
    assert universe.candidates(desk,dict(p,universe='focus'),0,NOW)==['AAA','BBB']
    assert p['symbols']==['AAA','BBB']


def test_radar_directory_batch_is_bounded_rotating_and_normalizes_classes(isolated,monkeypatch):
    import market_discovery
    monkeypatch.setattr(market_discovery,'symbols',lambda *a,**k:['RANKED'])
    desk._save_json(isolated,{'symbols':[{'symbol':'BRK.B'}]+[{'symbol':f'S{i}'} for i in range(100)],'updated_at':NOW.isoformat()})
    first=universe.radar_candidates(desk,NOW)
    second=universe.radar_candidates(desk,NOW+timedelta(minutes=5))
    assert len(first)<=80 and first[0]=='RANKED' and first!=second
    assert 'BRK-B' in first+second


@pytest.mark.parametrize('batch', [2, 3, 4, 8])
def test_persistent_shared_discovery_does_not_starve_directory_positions(isolated, monkeypatch, batch):
    import market_discovery
    directory = ['A', 'B', 'C', 'D']
    desk._save_json(isolated, {'symbols':[{'symbol':s} for s in directory], 'updated_at':NOW.isoformat()})
    monkeypatch.setattr(market_discovery, 'symbols', lambda *a, **k:['RANKED'])
    p = {'universe':'broad_us', 'symbols':['F'], 'candidates_per_cycle':batch}
    batches = [universe.candidates(desk, p, cycle*batch, NOW) for cycle in range(8)]
    assert set(directory) <= {s for group in batches for s in group}
    assert all(group[0] == 'F' and len(group) <= batch and len(set(group)) == len(group) for group in batches)


def test_radar_uses_discovery_only_as_symbols_for_new_quotes(monkeypatch):
    import market_radar
    for name in ('fetch_alpaca_snapshot_movers','fetch_polygon_snapshot_movers','fetch_yahoo_screener_movers'):
        monkeypatch.setattr(market_radar,name,lambda *a,**k:[])
    calls=[]
    monkeypatch.setattr(market_radar,'_discovery_provider',lambda:['SHARED'])
    monkeypatch.setattr(market_radar,'fetch_batch_quote_movers',lambda **kw:calls.append(kw) or [])
    result=market_radar.scan_movers()
    assert calls==[{'universe':['SHARED']}] and result['movers']==[]
