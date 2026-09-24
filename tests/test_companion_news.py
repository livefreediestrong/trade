from datetime import datetime, timezone
from types import SimpleNamespace
import copy
import threading

import pytest
import companion_news as news

NOW=datetime(2026,9,24,6,tzinfo=timezone.utc).timestamp()
SOURCE=news.SOURCES[0]


def rss(title='Markets consider a new rate outlook', date='Thu, 24 Sep 2026 05:00:00 GMT', link='https://www.cnbc.com/story'):
    return f'<rss><channel><item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()


def test_ten_sources_keep_ownership_and_agency_distinctions():
    assert len(news.SOURCES)==10 and len({s['id'] for s in news.SOURCES})==10
    assert next(s for s in news.SOURCES if s['id']=='wsj')['group']==next(s for s in news.SOURCES if s['id']=='marketwatch')['group']
    assert len(next(s for s in news.SOURCES if s['id']=='bls')['feeds'])==2


def test_bad_optional_cache_does_not_touch_trading_corruption_flags(tmp_path):
    (tmp_path/'companion_news.json').write_text('{broken',encoding='utf-8')
    desk=SimpleNamespace(DATA_DIR=tmp_path,_load_json=lambda *a:pytest.fail('Do not route optional cache through trading state'))
    assert not news.HeadlineDesk(desk).cache


def test_rss_and_atom_keep_actual_publication_dates_and_attribution():
    rows,rejected=news.parse_feed(rss(),SOURCE,NOW)
    assert rejected==0 and len(rows)==1 and rows[0]['source']=='CNBC'
    assert rows[0]['published_ts']==NOW-3600
    atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Employment situation</title><link href="https://www.bls.gov/news.release/empsit.nr0.htm"/><updated>2026-09-23T12:30:00Z</updated></entry></feed>'
    assert len(news.parse_feed(atom,next(s for s in news.SOURCES if s['id']=='bls'),NOW)[0])==1


@pytest.mark.parametrize('date',['Thu, 24 Sep 2026 07:00:00 GMT','bad','2026-09-24T05:00:00',''])
def test_future_missing_or_ambiguous_dates_are_excluded(date):
    assert news.parse_feed(rss(date=date),SOURCE,NOW)==([],1)


@pytest.mark.parametrize('url',['javascript:alert(1)','https://cnbc.com.attacker.test/story','http://127.0.0.1:5056/api/config','https://user:pass@cnbc.com/story','https://cnbc.com:9999/story'])
def test_unsafe_or_unattributable_links_are_rejected(url):
    assert news.safe_link(url,SOURCE['domains']) is None
    assert news.parse_feed(rss(link=url),SOURCE,NOW)==([],1)


@pytest.mark.parametrize('body',[b'<html>Login required</html>',b'<!DOCTYPE rss><rss/>',b'<!ENTITY x "unsafe"><rss/>',b'x'*(news.MAX_BYTES+1)],ids=['html','doctype','entity','oversize'])
def test_nonfeed_or_unbounded_content_cannot_be_accepted(body):
    with pytest.raises(ValueError):news.parse_feed(body,SOURCE,NOW)


def service(tmp_path):
    saves=[]
    desk=SimpleNamespace(DATA_DIR=tmp_path,_load_json=lambda p,d:d,_save_json=lambda p,d:saves.append(copy.deepcopy(d)))
    return news.HeadlineDesk(desk),saves


def test_failed_feed_retains_old_rows_without_making_them_fresh(tmp_path,monkeypatch):
    worker,saves=service(tmp_path)
    rows,_=news.parse_feed(rss(),SOURCE,NOW)
    worker.cache={'cnbc':{'items':rows,'status':'connected','checked_at':NOW}}
    monkeypatch.setattr(news,'fetch_source',lambda s,n:{'items':[],'status':'unavailable','error':'HTTP 403','checked_at':NOW,'rejected':0})
    worker._work()
    state=worker.snapshot(NOW)
    assert state['items'][0]['fresh'] is False
    assert state['sources'][0]['status']=='unavailable'
    assert state['items'][0]['published_ts']==NOW-3600
    assert saves and not worker.busy


def test_old_publications_and_expired_checks_do_not_become_quips(tmp_path):
    worker,_=service(tmp_path)
    rows,_=news.parse_feed(rss(),SOURCE,NOW)
    worker.cache={'cnbc':{'items':rows,'status':'connected','checked_at':NOW-901}}
    assert worker.snapshot(NOW)['items'][0]['fresh'] is False
    worker.cache['cnbc']['checked_at']=NOW+1
    assert worker.snapshot(NOW)['items'][0]['fresh'] is False
    worker.cache['cnbc']['checked_at']=NOW+40*3600
    state=worker.snapshot(NOW+40*3600)
    assert not state['items'] and state['sources'][0]['status']=='connected'


def test_fresh_syndicated_headline_wins_over_earlier_failed_source(tmp_path):
    worker, _ = service(tmp_path)
    rows, _ = news.parse_feed(rss(), SOURCE, NOW)
    yahoo = next(s for s in news.SOURCES if s['id'] == 'yahoo')
    fresh = dict(rows[0], source=yahoo['name'], source_id=yahoo['id'], url='https://finance.yahoo.com/news/story')
    worker.cache = {'cnbc':{'items':rows,'status':'unavailable','checked_at':NOW},
                    yahoo['id']:{'items':[fresh],'status':'connected','checked_at':NOW}}
    result = worker.snapshot(NOW)['items']
    assert len(result) == 1 and result[0]['fresh']
    assert result[0]['source'] == yahoo['name'] and result[0]['url'] == fresh['url']


def test_refresh_is_single_flight_and_rate_limited(tmp_path,monkeypatch):
    worker,_=service(tmp_path);entered=threading.Event();release=threading.Event();done=threading.Event()
    def work():
        entered.set();release.wait(3);worker.busy=False;done.set()
    monkeypatch.setattr(worker,'_work',work)
    assert worker.refresh();assert entered.wait(1)
    assert not worker.refresh()
    release.set();assert done.wait(1)
    assert not worker.refresh()


def test_endpoint_is_read_only_and_background_refresh_is_bounded(monkeypatch):
    import app as desk
    service=desk._research_companion.news
    calls=[]
    monkeypatch.setattr(service,'refresh',lambda:calls.append('refresh'))
    monkeypatch.setattr(service,'snapshot',lambda:{'ok':True,'sources':[],'items':[]})
    monkeypatch.setattr(desk,'save_config',lambda *a:pytest.fail('Configuration write forbidden'))
    monkeypatch.setattr(desk,'live_broker_place_order',lambda *a:pytest.fail('Execution forbidden'))
    response=desk.app.test_client().get('/api/companion/news',base_url='http://127.0.0.1:5056')
    assert response.status_code==200 and response.json['ok'] and calls==['refresh']
