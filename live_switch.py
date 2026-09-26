"""One plain Live trading ON/OFF switch (docs/LIVE_SWITCH.md).

OFF always returns the desk to `manual`: no broker orders from this desk (no Fox trades,
no tickets, no automatic exits) and Fox is switched off. OFF needs no confirmation
because it only removes risk.

ON only ever selects `live_manual` (you approve every order). It can never arm
`auto_live`; Fox is started separately on the Fox page. ON goes through the same
`_api_config_post` gate as the Settings mode menu (verified broker identity plus the
REAL / AUTO_LIVE token), after one typed confirmation, GO LIVE.
"""
from __future__ import annotations

from typing import Any

LIVE_MODES = ("live_manual", "auto_live")
CONFIRM = "GO LIVE"


def view(cfg: dict[str, Any], broker: dict[str, Any]) -> dict[str, Any]:
    mode = cfg.get("mode") or "manual"
    live = mode in LIVE_MODES
    real = bool(broker.get("configured")) and broker.get("paper_mode") is False
    account = "your real-money account" if real else "your broker's paper account" if broker.get("configured") else "no broker"
    if not live:
        plain = ("OFF. This desk sends no orders to your broker: no Fox trades, no tickets and no automatic exits. "
                 "Anything already open stays open at your broker.")
    elif mode == "auto_live":
        plain = f"ON with Fox trading automatically in {account}. Turn OFF to stop all new broker orders from this desk."
    else:
        plain = f"ON. You approve every order before it goes to {account}. Fox only trades if you start him on the Fox page."
    return {"ok": True, "live": live, "mode": mode, "real_money": real, "broker_configured": bool(broker.get("configured")),
            "plain": plain, "confirm_phrase": CONFIRM}


def register(app, desk) -> None:
    from flask import jsonify, request

    def current():
        with desk._lock:
            cfg = desk.load_config()
        return view(cfg, desk._broker_public_status())

    @app.get("/api/live/master")
    def live_master_status():
        return jsonify(current())

    @app.post("/api/live/master")
    def live_master_set():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("live"), bool) or set(body) - {"live", "confirm"}:
            return jsonify({"ok": False, "error": "Send live: true or false (and confirm when turning on)"}), 400
        with desk._BROKER_EXEC_LOCK, desk._lock:
            cfg = desk.load_config()
            if not body["live"]:
                if cfg.get("mode") not in LIVE_MODES and not (cfg.get("live_agent") or {}).get("enabled"):
                    return jsonify(current())
                res = desk._api_config_post(cfg, {"mode": "manual"})
                if not (isinstance(res, tuple) and len(res) == 4):
                    return res
                desk.append_journal("live_master_off", {"previous_mode": cfg.get("mode")})
            else:
                if cfg.get("mode") in LIVE_MODES:
                    return jsonify(current())  # already live; never downgrades or upgrades Fox
                broker = desk._broker_public_status()
                if not broker.get("configured"):
                    return jsonify({"ok": False, "error": "Connect a broker in Settings before turning live trading on."}), 409
                if str(body.get("confirm") or "").strip().upper() != CONFIRM:
                    return jsonify({"ok": False, "error": f"Type {CONFIRM} to turn live trading on."}), 400
                token = "REAL" if broker.get("paper_mode") is False else "AUTO_LIVE"
                try:
                    res = desk._api_config_post(cfg, {"mode": "live_manual", "live_confirm": token})
                except desk.BadNumber as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 400
                if not (isinstance(res, tuple) and len(res) == 4):
                    return res  # the shared gate refused (identity, token or broker check)
                desk.append_journal("live_master_on", {"mode": "live_manual", "real_money": broker.get("paper_mode") is False})
        desk._api_config_after(*res)
        return jsonify(current())
