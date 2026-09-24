"""Exercise SSE through the actual production WSGI server, not Flask alone."""
import threading
import time

import pytest
import requests
from waitress import create_server

import app as desk


def test_sse_opens_through_waitress(monkeypatch):
    monkeypatch.setattr(desk, "_build_state_lite", lambda: {"ok": True})
    monkeypatch.setattr(desk, "_SSE_SLOTS", threading.BoundedSemaphore(desk._SSE_STREAM_LIMIT))
    server = create_server(desk.app, host="127.0.0.1", port=0, threads=2)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        with requests.get(f"http://127.0.0.1:{server.effective_port}/api/loop/stream", headers={"Host": "127.0.0.1:5056"}, stream=True, timeout=5) as response:
            assert response.status_code == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            lines = response.iter_lines(chunk_size=1)
            assert next(lines) == b"retry: 3000"
            assert next(lines) == b""
            assert next(lines) == b"event: hello"
    finally:
        server.close()
        thread.join(timeout=5)


def test_sse_capacity_keeps_controls_responsive_and_recovers_after_disconnect(monkeypatch):
    """Keep every admitted stream open while exceeding the production pool size."""
    monkeypatch.setattr(desk, "_build_state_lite", lambda: {"ok": True})
    monkeypatch.setattr(desk, "_SSE_SLOTS", threading.BoundedSemaphore(desk._SSE_STREAM_LIMIT))
    assert 0 < desk._SSE_STREAM_LIMIT < desk._HTTP_WORKER_THREADS
    server = create_server(desk.app, host="127.0.0.1", port=0, threads=desk._HTTP_WORKER_THREADS)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.effective_port}"
    headers = {"Host": "127.0.0.1:5056"}
    streams = []
    # Retain iterators too: garbage collection can otherwise close their sockets,
    # making a test appear to hold streams open when it actually does not.
    iterators = []
    try:
        for _ in range(desk._SSE_STREAM_LIMIT):
            response = requests.get(base + "/api/loop/stream", headers=headers, stream=True, timeout=2)
            streams.append(response)
            assert response.status_code == 200
            lines = response.iter_lines(chunk_size=1)
            iterators.append(lines)
            assert next(lines) == b"retry: 3000"
        for _ in range(desk._HTTP_WORKER_THREADS):
            with requests.get(base + "/api/loop/stream", headers=headers, timeout=2) as denied:
                assert denied.status_code == 503
                assert denied.json()["fallback"] == "polling"
                assert denied.headers["Retry-After"] == "15"
        with requests.get(base + "/api/health", headers=headers, timeout=2) as health:
            assert health.status_code == 200
            assert health.json()["app_id"] == "tomahawk-desk"

        # The next write discovers this real socket disconnect and releases its
        # slot. A replacement must be admitted without restarting the server.
        streams.pop().close()
        iterators.pop().close()
        deadline = time.monotonic() + 8
        while True:
            response = requests.get(base + "/api/loop/stream", headers=headers, stream=True, timeout=2)
            if response.status_code == 200:
                streams.append(response)
                lines = response.iter_lines(chunk_size=1)
                iterators.append(lines)
                assert next(lines) == b"retry: 3000"
                break
            response.close()
            assert time.monotonic() < deadline, "Disconnected stream did not release its slot"
            time.sleep(0.1)
    finally:
        for response in streams:
            response.close()
        for lines in iterators:
            lines.close()
        server.close()
        thread.join(timeout=5)


@pytest.mark.parametrize("failure", ["unconsumed", "generator", "response_creation"])
def test_sse_slot_released_on_early_close_or_failure(monkeypatch, failure):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(desk, "_SSE_SLOTS", slots)

    def fail(*args, **kwargs):
        raise RuntimeError("stream failed")

    with desk.app.test_request_context("/api/loop/stream"):
        if failure == "response_creation":
            import flask
            monkeypatch.setattr(flask, "Response", fail)
            with pytest.raises(RuntimeError, match="stream failed"):
                desk.api_loop_stream()
        else:
            response = desk.api_loop_stream()
            if failure == "generator":
                monkeypatch.setattr(desk, "_json_safe", fail)
                chunks = iter(response.response)
                assert next(chunks) == "retry: 3000\n\n"
                with pytest.raises(RuntimeError, match="stream failed"):
                    next(chunks)
            response.close()
            response.close()  # cleanup callbacks must not release a slot twice
    assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)
    slots.release()
