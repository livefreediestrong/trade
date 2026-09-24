"""Source restoration checks use only temporary settings and intercepted sockets."""
import errno
import os

import pytest
import data_sources
import release_tools as release


@pytest.fixture
def source(tmp_path, monkeypatch):
    for name in ('TOMAHAWK_PORT', 'TOMAHAWK_HOST', 'TOMAHAWK_DATA_DIR', 'TOMAHAWK_INSTANCE_LOCK'):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path/'app'; root.mkdir()
    (root/'app.py').write_text('version = 1\n')
    archive = tmp_path/'release.zip'
    release.pack(root, archive)
    (root/'app.py').write_text('version = 2\n')
    return root, archive


def fake_socket(monkeypatch, code=errno.ECONNREFUSED):
    checked = []
    class Socket:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, *a): pass
        def connect_ex(self, endpoint):
            checked.append(endpoint)
            return code
    monkeypatch.setattr(release.socket, 'socket', Socket)
    return checked


@pytest.mark.parametrize('process_override', [False, True])
def test_restore_checks_configured_endpoint_before_writing(source, monkeypatch, process_override):
    root, archive = source
    (root/'.env').write_text('TOMAHAWK_HOST=192.0.2.2\nTOMAHAWK_PORT=5057 # configured\n')
    if process_override:
        monkeypatch.setenv('TOMAHAWK_HOST', '192.0.2.3')
        monkeypatch.setenv('TOMAHAWK_PORT', '5058')
    checked = fake_socket(monkeypatch, 0)
    with pytest.raises(ValueError, match='Stop the desk'):
        release.restore(archive, root, apply=True)
    assert checked == [('192.0.2.3', 5058) if process_override else ('192.0.2.2', 5057)]
    assert (root/'app.py').read_text() == 'version = 2\n'


def test_explicit_endpoint_override_retains_durable_state(source, monkeypatch):
    root, archive = source
    settings = 'TOMAHAWK_PORT=5057\nTOMAHAWK_DATA_DIR="data # custom"\n'
    (root/'.env').write_text(settings)
    checked = fake_socket(monkeypatch)
    release.restore(archive, root, apply=True, port=5059, host='127.0.0.2')
    assert checked == [('127.0.0.2', 5059)]
    assert (root/'app.py').read_text() == 'version = 1\n'
    assert (root/'.env').read_text() == settings


@pytest.mark.parametrize('pid_text', ['1234', 'malformed'])
def test_instance_lock_blocks_before_endpoint_check(source, monkeypatch, pid_text):
    root, archive = source
    (root/'.env').write_text('TOMAHAWK_DATA_DIR="data # custom"\n')
    data = root/'data # custom'; data.mkdir(); (data/'tomahawk.pid').write_text(pid_text)
    monkeypatch.setattr(release, '_pid_is_running', lambda pid: pid == 1234)
    checked = fake_socket(monkeypatch)
    with pytest.raises(ValueError, match='instance PID|Instance lock'):
        release.restore(archive, root, apply=True)
    assert checked == [] and (root/'app.py').read_text() == 'version = 2\n'


@pytest.mark.parametrize('host,expected', [('0.0.0.0','127.0.0.1'), ('::','::1')])
def test_wildcard_bind_and_stale_pid_check(source, monkeypatch, host, expected):
    root, archive = source
    (root/'.env').write_text(f'TOMAHAWK_HOST={host}\nTOMAHAWK_INSTANCE_LOCK=custom.pid\n')
    (root/'custom.pid').write_text('1234')
    monkeypatch.setattr(release, '_pid_is_running', lambda pid: False)
    checked = fake_socket(monkeypatch)
    assert release.restore(archive, root, apply=True)['applied']
    assert checked == [(expected, 5056)]


def test_inconclusive_endpoint_check_does_not_restore(source, monkeypatch):
    root, archive = source
    fake_socket(monkeypatch, errno.ETIMEDOUT)
    with pytest.raises(ValueError, match='cannot verify'):
        release.restore(archive, root, apply=True)
    assert (root/'app.py').read_text() == 'version = 2\n'


def test_python_env_loader_handles_comments_and_preserves_process_values(tmp_path, monkeypatch):
    path = tmp_path/'.env'
    values = {'TEST_ENV_PORT':'4002', 'TEST_ENV_HASH':'value # retained', 'TEST_ENV_INLINE':'abc#def',
              'TEST_ENV_QUOTE':'abc" # def', 'TEST_ENV_PRIORITY':'process'}
    path.write_text('TEST_ENV_PORT=4002 # paper\nTEST_ENV_HASH="value # retained" # "comment"\n'
                    'TEST_ENV_INLINE=abc#def\nTEST_ENV_QUOTE="abc\\" # def"\nTEST_ENV_PRIORITY=file\n')
    for key in values:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('TEST_ENV_PRIORITY', 'process')
    monkeypatch.setattr(data_sources, '_env_search_paths', lambda: [path])
    data_sources._load_env()
    assert {key:os.environ[key] for key in values} == values
