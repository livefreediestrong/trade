"""Browser file updates must not reconnect the broker or reuse an older pass."""
import os

from jinja2 import FileSystemLoader
import pytest

import app as desk
import code_version

BASE = "http://127.0.0.1:5056"


def write(path, text):
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    # Windows can otherwise retain the timestamp of a same-size fast write.
    stamp = max(path.stat().st_mtime_ns, before + 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    for name, content in {"app.py": "x = 1\n", "static/ui.js": "let a=1;",
                          "static/ui.css": "body {color: red}",
                          "templates/pages/overview.html": "<p>Before update</p>"}.items():
        write(tmp_path / name, content)
    full, backend = code_version.fingerprint, code_version.backend_fingerprint
    monkeypatch.setattr(code_version, "fingerprint", lambda: full(tmp_path))
    monkeypatch.setattr(code_version, "backend_fingerprint", lambda: backend(tmp_path))
    monkeypatch.setattr(code_version, "_digests", {})
    monkeypatch.setattr(code_version, "_state", {"started": None, "current": None, "checked": 0.0})
    code_version.mark_started()
    return tmp_path


@pytest.mark.parametrize("file", ["static/ui.js", "static/ui.css", "templates/pages/overview.html"])
def test_browser_update_changes_full_receipt_without_restart(source_tree, file):
    before = code_version.status()
    write(source_tree / file, "Updated browser file")
    after = code_version.status(force=True)
    assert after["started"] == before["started"]
    assert after["on_disk"] != before["on_disk"]
    assert after["serving"] == after["on_disk"]
    assert after["browser_files_changed"] is True
    assert after["stale"] is False and after["message"] is None
    assert desk._startup_status()["code_stale"] is False


@pytest.mark.parametrize("change", ["edit", "add", "delete", "mixed"])
def test_loaded_python_change_still_requires_restart(source_tree, change):
    if change in ("edit", "mixed"):
        write(source_tree / "app.py", "x = 2\n")
    elif change == "add":
        write(source_tree / "new_module.py", "x = 1\n")
    else:
        (source_tree / "app.py").unlink()
    if change == "mixed":
        write(source_tree / "static/ui.js", "let a=2;")
    result = code_version.status(force=True)
    assert result["stale"] is True and result["serving"] is None
    assert result["browser_files_changed"] is False
    assert "Reopen the desktop shortcut" in result["message"]


def test_same_process_serves_changed_template_and_static_file(source_tree, monkeypatch):
    # Exercise the real Flask route, Jinja reload and static conditional response,
    # not just version comparisons. No worker, broker or server process starts.
    monkeypatch.setattr(desk.app, "static_folder", str(source_tree / "static"))
    monkeypatch.setattr(desk.app.jinja_env, "loader", FileSystemLoader(source_tree / "templates"))
    monkeypatch.setattr(desk.app.jinja_env, "cache", {})
    assert desk.app.jinja_env.auto_reload is True
    client = desk.app.test_client()
    before_page = client.get("/desk/overview", base_url=BASE)
    before_asset = client.get("/static/ui.js", base_url=BASE)
    assert before_page.status_code == before_asset.status_code == 200
    assert b"Before update" in before_page.data and b"let a=1;" in before_asset.data
    write(source_tree / "templates/pages/overview.html", "<p>After update</p>")
    write(source_tree / "static/ui.js", "let a=2;")
    after_page = client.get("/desk/overview", base_url=BASE)
    after_asset = client.get("/static/ui.js", base_url=BASE,
                             headers={"If-None-Match": before_asset.headers["ETag"]})
    assert b"After update" in after_page.data
    assert after_asset.status_code == 200 and b"let a=2;" in after_asset.data
    assert after_asset.headers["ETag"] != before_asset.headers["ETag"]
    assert code_version.status(force=True)["stale"] is False


def test_digest_cache_does_not_confuse_two_source_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(code_version, "_digests", {})
    a, b = tmp_path / "first/app.py", tmp_path / "second/app.py"
    write(a, "x=1"); write(b, "x=2")
    os.utime(b, ns=(a.stat().st_mtime_ns, a.stat().st_mtime_ns))
    assert code_version.fingerprint(a.parent) != code_version.fingerprint(b.parent)


def test_probe_revision_invalidates_previous_test_receipt(tmp_path):
    path = tmp_path / "tools/pnl_probe.py"
    write(path, "version = 1\n")
    previous = code_version.test_fingerprint(tmp_path)
    write(path, "version = 2\n")
    assert code_version.test_fingerprint(tmp_path) != previous
