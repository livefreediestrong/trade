from types import SimpleNamespace as NS
import pytest
import local_chat


@pytest.fixture(autouse=True)
def reset():
    local_chat.CACHE.update(at=0, value=None)


def test_no_download_remote_models_redirect_or_proxy(monkeypatch):
    calls = []
    class Session:
        trust_env = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            assert self.trust_env is False and kw['allow_redirects'] is False
            calls.append(url)
            return NS(status_code=200, json=lambda: {"models": [{"name": "safe:latest", "size": 2e9, "details": {"format": "gguf"}}, {"name": "remote:cloud", "size": 3e9, "details": {"format": "gguf"}}]})
        def post(self, url, **kw):
            calls.append(url)
            if url.endswith('/api/show'):
                return NS(status_code=200, json=lambda: {"remote_host": "https://external", "model_info": {"a": 1}, "details": {"format": "gguf"}})
            pytest.fail('No chat allowed for a remote model')
    monkeypatch.setattr(local_chat.requests, 'Session', Session)
    assert [m['name'] for m in local_chat.status()['models']] == ['safe:latest']
    with pytest.raises(ValueError, match='installed'): local_chat.generate('test', 'system', 'remote:cloud')
    with pytest.raises(ValueError, match='verified local'): local_chat.generate('test', 'system', 'safe:latest')
    assert all(u.startswith('http://127.0.0.1:11434/') for u in calls)
    assert all('/pull' not in u for u in calls)


def test_context_bound_prevents_silent_local_truncation(monkeypatch):
    monkeypatch.setattr(local_chat, 'status', lambda: {'models': [{'name': 'fixture'}]})
    with pytest.raises(ValueError, match='too long'):
        local_chat.generate('x' * 6501, '', 'fixture')
