"""Probe refuses unintended connections; official decoder receives test data only."""
import json
from types import SimpleNamespace as NS
from urllib.error import URLError
import pytest
from tools import pnl_probe as probe


def test_default_is_a_plan_without_import_or_connection(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(probe, "desk_is_stopped", lambda *_: pytest.fail("No network in plan mode"))
    path = tmp_path / "report.json"
    assert probe.main(["--output", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["connected"] is False
    assert not path.exists()


@pytest.mark.parametrize("args", [["--port", "4001"], ["--port", "7496"], ["--client-id", "0"],
                                  ["--client-id", "37"], ["--cycles", "4"], ["--seconds", "0"]])
def test_invalid_or_live_target_is_rejected(args):
    with pytest.raises((probe.ProbeError, SystemExit)):
        probe.main(args)


@pytest.mark.parametrize("accounts,chosen", [(["U-PRIVATE"], ""), (["DU-A", "DU-B"], ""),
                                           (["DU-A"], "DU-MISSING"), (["U-PRIVATE"], "U-PRIVATE")])
def test_account_guard_does_not_print_account(accounts, chosen):
    with pytest.raises(probe.ProbeError) as exc:
        probe.select_paper_account(accounts, chosen)
    assert not any(account in str(exc.value) for account in accounts)


def test_selected_paper_account_must_be_advertised():
    assert probe.select_paper_account(["DU-A", "DU-B"], "DU-B") == "DU-B"


def test_running_desk_or_uncertain_status_blocks_probe(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(probe, "urlopen", lambda *a, **k: Response())
    with pytest.raises(probe.ProbeError, match="desk is running"):
        probe.desk_is_stopped(5056)
    def timeout(*a, **k): raise URLError(TimeoutError())
    monkeypatch.setattr(probe, "urlopen", timeout)
    with pytest.raises(probe.ProbeError, match="Cannot confirm"):
        probe.desk_is_stopped(5056)
    def refused(*a, **k): raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(probe, "urlopen", refused)
    probe.desk_is_stopped(5056)


def test_receipt_handles_late_invalid_and_zero_without_leaking_values():
    receipt = probe.Receipt()
    receipt.pnl(9001, 432.25)
    receipt.arm()
    receipt.pnl(1, 432.25)
    for missing in (None, float("nan"), float("inf"), 1.7976931348623157e308):
        receipt.pnl(9001, missing)
    assert receipt.snapshot()["result"] == "missing"
    receipt.pnl(9001, 0.)
    receipt.disarm()
    receipt.pnl(9001, 432.25)
    snap = receipt.snapshot()
    assert snap["usable_callbacks"] == 1 and snap["ignored_callbacks"] == 3
    assert "432.25" not in json.dumps(snap) and "daily" not in snap


def test_official_legacy_and_protobuf_decoders_reach_receipt():
    # SDK is optional for ordinary desk tests; the revision qualification installs it.
    pytest.importorskip("ibapi")
    from ibapi.decoder import Decoder
    from ibapi.protobuf.PnL_pb2 import PnL
    receipt = probe.Receipt()
    client = probe.official_client(receipt)
    receipt.arm()
    decoder = Decoder(client, 178)
    decoder.processPnLMsg(iter([b"9001", b"-2.5", b"0", b"0"]))
    data = PnL(reqId=9001, dailyPnL=0.)
    decoder.processPnLMsgProtoBuf(data.SerializeToString())
    assert receipt.snapshot()["usable_callbacks"] == 2
    client.error(-1, 0, 1100, "U-PRIVATE sensitive error")
    assert receipt.snapshot()["upstream_available"] is False
    client.error(-1, 0, 1102, "U-PRIVATE sensitive error")
    assert receipt.snapshot()["upstream_available"] is True
    assert "U-PRIVATE" not in json.dumps(receipt.snapshot())


def test_failed_cycle_keeps_redacted_receipt_and_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.logging, "disable", lambda *_: None)
    monkeypatch.setattr(probe, "desk_is_stopped", lambda *_: None)
    calls = []
    def fail(args, receipt):
        calls.append(1)
        receipt.arm()
        receipt.current_time()
        raise RuntimeError("U-PRIVATE account details")
    monkeypatch.setattr(probe, "observe_official", fail)
    path = tmp_path / "report.json"
    with pytest.raises(probe.ProbeError, match="RuntimeError"):
        probe.main(["--run-paper", "--output", str(path)])
    assert calls == [1]
    text = path.read_text()
    assert "U-PRIVATE" not in text
    data = json.loads(text)
    assert data["cycles"][0]["time_replies"] == 1
    assert data["cycles"][0]["completed"] is False


def test_cycles_get_separate_receipts_and_missing_callback_is_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.logging, "disable", lambda *_: None)
    monkeypatch.setattr(probe, "desk_is_stopped", lambda *_: None)
    monkeypatch.setattr(probe.time, "sleep", lambda *_: None)
    receipts = []
    def observe(args, receipt):
        receipts.append(receipt)
        receipt.arm()
        if len(receipts) == 1:
            receipt.pnl(9001, 0.)
        return {"connected_at_end": True, **receipt.snapshot()}
    monkeypatch.setattr(probe, "observe_official", observe)
    path = tmp_path / "report.json"
    assert probe.main(["--run-paper", "--cycles", "2", "--output", str(path)]) == 3
    assert receipts[0] is not receipts[1]
    assert [r["result"] for r in json.loads(path.read_text())["cycles"]] == ["received", "missing"]
    with pytest.raises(probe.ProbeError, match="already exists"):
        probe.main(["--run-paper", "--output", str(path)])


@pytest.mark.parametrize("failure", ["", "request", "cancel", "account"])
def test_official_probe_cleanup_only_cancels_its_data_and_disconnects(monkeypatch, failure):
    pytest.importorskip("ibapi")
    receipt = probe.Receipt()
    calls = []
    clock = [0.]
    ready = NS(wait=lambda *_: True)
    def request(req_id, account, model):
        calls.append(("subscribe", req_id))
        if failure == "request": raise RuntimeError("U-PRIVATE")
        receipt.pnl(req_id, 0.)
    def cancel(req_id):
        calls.append(("cancel-data", req_id))
        if failure == "cancel": raise RuntimeError("U-PRIVATE")
    client = NS(connect=lambda *a, **k: calls.append(("connect", k["clientId"])),
                run=lambda: None, handshake=ready, accounts_ready=ready,
                accounts=["U-PRIVATE" if failure == "account" else "DU-TEST"],
                reqPnL=request, cancelPnL=cancel, isConnected=lambda: True,
                reqCurrentTime=receipt.current_time, serverVersion=lambda: 178,
                disconnect=lambda: calls.append(("disconnect",)))
    thread = NS(start=lambda: None, join=lambda **k: None, is_alive=lambda: False)
    monkeypatch.setattr(probe, "official_client", lambda *_: client)
    monkeypatch.setattr(probe.threading, "Thread", lambda **k: thread)
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe.time, "sleep", lambda *_: clock.__setitem__(0, clock[0] + 20))
    monkeypatch.delenv("PNL_PROBE_ACCOUNT", raising=False)
    args = NS(port=4002, client_id=937, seconds=10)
    if failure:
        with pytest.raises((RuntimeError, probe.ProbeError)):
            probe.observe_official(args, receipt)
    else:
        result = probe.observe_official(args, receipt)
        assert result["usable_callbacks"] == 1 and result["time_replies"] == 1
    assert calls[-1] == ("disconnect",)
    assert receipt.active is False
    if failure == "account":
        assert calls == [("connect", 937), ("disconnect",)]
    else:
        assert calls[-2] == ("cancel-data", 9001)
