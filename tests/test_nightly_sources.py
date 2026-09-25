from types import SimpleNamespace as NS

import nightly_sources as sources


def test_official_release_rejects_redirects_and_unapproved_hosts(monkeypatch):
    calls=[]
    class Response:
        status_code=302
        headers={"Content-Type":"text/html", "Location":"http://127.0.0.1/private"}
        def __enter__(self): return self
        def __exit__(self,*a): pass
    def get(url,**kw):
        calls.append(url)
        assert kw["allow_redirects"] is False
        return Response()
    monkeypatch.setattr(sources.requests,"get",get)
    row={"id":"1","title":"Release","source_id":"fed","url":"https://www.federalreserve.gov/release"}
    assert sources.read_release(row)["status"] == "unavailable"
    assert sources.read_release(dict(row,url="https://federalreserve.gov.evil.example/"))["status"] == "unavailable"
    assert len(calls)==1


def test_extracted_text_bounded_and_script_removed(monkeypatch):
    class Response:
        status_code=200
        headers={"Content-Type":"text/html"}
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def iter_content(self,size):
            yield ('<nav><p>Navigation</p></nav><script>unsafe()</script><main><h1>Release</h1><p>'+('Reported observation. '*400)+'</p></main>').encode()
    monkeypatch.setattr(sources.requests,"get",lambda *a,**kw:Response())
    row=sources.read_release({"id":"1","title":"Release","source_id":"fed","url":"https://www.federalreserve.gov/release"})
    assert row["status"] == "read" and row["truncated"] and len(row["text"])==5000
    assert "unsafe" not in row["text"] and "Navigation" not in row["text"]


def test_primary_selection_stale_future_commercial_and_limit(monkeypatch):
    rows=[{"id":str(i),"fresh":True,"kind":"Official release","published_ts":99} for i in range(8)]
    rows += [{"id":"future","fresh":True,"kind":"Official release","published_ts":101},
             {"id":"stale","fresh":False,"kind":"Official release","published_ts":99},
             {"id":"commercial","fresh":True,"kind":"Reporting","published_ts":99}]
    monkeypatch.setattr(sources,"read_release",lambda row:row)
    assert [r["id"] for r in sources.read_primary_releases(rows,100)] == ['0','1','2','3']
