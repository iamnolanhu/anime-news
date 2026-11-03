"""The credit meter counts each Router job's charge once, even when several responses of the job carry it."""
import json

from shot_director import router_client as rc


class FakeResponse:
    def __init__(self, status, doc, credits=None):
        self.status = status
        self.headers = {"X-Comfy-Credits-Used": credits} if credits else {}
        self._body = json.dumps(doc).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_meter_dedupes_by_job_and_counts_unkeyed_calls():
    m = rc.CreditMeter()
    m.record({"X-Comfy-Credits-Used": "14.48"}, "job-1")
    m.record({"X-Comfy-Credits-Used": "14.48"}, "job-1")
    m.record({"X-Comfy-Credits-Used": "2"}, "job-2")
    m.record({"X-Comfy-Credits-Used": "1"})
    m.record({"X-Comfy-Credits-Used": "1"})
    assert m.snapshot() == (14.48 + 2 + 2, 4)


def test_queued_job_charge_counts_once(monkeypatch, tmp_path):
    # Seen live 2026-11-03: the COMPLETED status poll and the result fetch both report the job's charge.
    replies = iter([FakeResponse(201, {"request_id": "r1"}),
                    FakeResponse(200, {"status": "COMPLETED"}, "14.48"),
                    FakeResponse(200, {"result": {"sample": "https://x/a.png"}}, "14.48")])
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: next(replies))
    before = rc.CREDITS.snapshot()
    rc.LiveRouter("k", tmp_path)._run_model("bfl/flux-3-image", {"prompt": "p"})
    after = rc.CREDITS.snapshot()
    assert round(after[0] - before[0], 2) == 14.48 and after[1] - before[1] == 1
