"""Optional installed Ollama models. Fixed loopback endpoint; no downloads or cloud fallback."""
import threading
import time
import requests

URL = "http://127.0.0.1:11434"
LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()
CACHE = {"at": 0, "value": None}


def session():
    s = requests.Session()
    s.trust_env = False  # Never send localhost conversation data through a proxy.
    return s


def status():
    with CACHE_LOCK:
        if CACHE["value"] is not None and 0 <= time.monotonic() - CACHE["at"] < 15:
            return CACHE["value"]
        try:
            with session() as s:
                r = s.get(URL + "/api/tags", timeout=2, allow_redirects=False)
                if r.status_code != 200:
                    raise ValueError("local_model_unavailable")
                raw = r.json().get("models", [])
            models = [{"name": m["name"], "size_gb": round(m["size"] / 1e9, 1), "parameters": m.get("details", {}).get("parameter_size")}
                      for m in raw if isinstance(m, dict) and isinstance(m.get("name"), str)
                      and "cloud" not in m["name"].lower() and not m.get("remote_host") and not m.get("remote_model")
                      and isinstance(m.get("size"), (int, float)) and m["size"] > 1_000_000
                      and m.get("details", {}).get("format") == "gguf"]
            value = {"available": bool(models), "models": models[:20], "note": "Installed local models only. Large models may slow this PC. No automatic cloud fallback or model downloads."}
        except Exception:
            value = {"available": False, "models": [], "note": "Local AI is not responding at 127.0.0.1:11434. Start Ollama with cloud features disabled. Local explanations still work."}
        CACHE.update(at=time.monotonic(), value=value)
        return value


def generate(prompt, system, model):
    if model not in {m["name"] for m in status()["models"]}:
        raise ValueError("Choose an installed local model. No model will be downloaded")
    # UTF-8 byte bound conservatively fits the 8K-token local window, including reply.
    if len((system + prompt).encode("utf-8")) > 6500:
        raise ValueError("This conversation is too long for the bounded local context. Start a new chat or use Gemini explicitly")
    if not LOCK.acquire(blocking=False):
        raise ValueError("Local AI is already preparing a reply; wait for it to finish")
    started = time.monotonic()
    try:
        with session() as s:
            meta = s.post(URL + "/api/show", json={"model": model}, timeout=5, allow_redirects=False)
            if meta.status_code != 200:
                raise ValueError("Local model metadata unavailable")
            info = meta.json()
            if info.get("remote_host") or info.get("remote_model") or not info.get("model_info") or info.get("details", {}).get("format") != "gguf":
                raise ValueError("Only a verified local GGUF model can receive this conversation")
            response = s.post(URL + "/api/chat", json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "stream": False, "think": False, "keep_alive": 0, "options": {"num_ctx": 8192, "num_predict": 600, "num_thread": 4, "temperature": .2}}, timeout=(5, 120), allow_redirects=False)
            if response.status_code != 200:
                raise ValueError("Local model could not complete the reply. No cloud request was sent")
            data = response.json()
            if data.get("done") is not True or data.get("done_reason") == "length" or not isinstance(data.get("message", {}).get("content"), str) or not data["message"]["content"].strip():
                raise ValueError("Local reply was empty or incomplete. No cloud fallback was used")
            return data["message"]["content"], round(time.monotonic() - started, 2)
    except requests.RequestException:
        raise ValueError("Local AI took too long or disconnected. It may still be finishing locally; no cloud fallback was used") from None
    finally:
        LOCK.release()
