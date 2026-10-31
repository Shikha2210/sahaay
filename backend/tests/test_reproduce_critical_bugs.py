"""Reproduction tests for the critical bugs found in the audit (documentation/BUGS_AND_GAPS.md).

Each test asserts the CORRECT behaviour and is marked xfail(strict=True):
  * while the bug exists the test fails -> reported as "xfailed" (bug reproduced);
  * once the bug is fixed the test passes -> strict xfail turns this into a failure,
    which tells us to delete the marker. That is intentional.
"""
import re
from unittest.mock import MagicMock
import pytest
from pymongo.errors import OperationFailure
from app import main


def frontend_create_fields(src):
    """Field names the frontend really sends when registering a patient offline."""
    m = re.search(r"operation_type:'create',changes:\{([^}]*)\}", src)
    assert m, "could not find the create-operation payload in App.jsx"
    return set(re.findall(r"(\w+):", m.group(1)))


def frontend_resolution_value(src):
    m = re.search(r"let payload=\{resolution:'([^']*)'", src)
    assert m, "could not find the resolve payload in App.jsx"
    return m.group(1)


@pytest.mark.xfail(strict=True, reason="S1: server rejects the create op because identity_status is not in SAFE_FIELDS")
def test_S1_offline_patient_create_from_frontend_is_not_rejected(as_role, app_jsx_source):
    fields = frontend_create_fields(app_jsx_source)
    assert "identity_status" in fields  # documents the cause
    client = as_role("worker")
    changes = {k: "x" for k in fields}
    changes["__create__"] = True
    r = client.post("/api/sync/batch", json={"operations": [
        {"operation_id": "op-s1", "patient_id": "SH-9999", "device_id": "d1", "base_version": 0, "changes": changes}]})
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] != "rejected", r.json()


@pytest.mark.xfail(strict=True, reason="C1: confirm-field endpoint is documented but not implemented")
def test_C1_patient_confirmation_route_exists():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert any("confirm" in p for p in paths), "no patient confirmation route registered"


@pytest.mark.xfail(strict=True, reason="C2: UI sends a free-text resolution; server only accepts accept_a/accept_b/custom -> 400")
def test_C2_value_sent_by_frontend_resolve_button_is_accepted(as_role, app_jsx_source, monkeypatch):
    sent = frontend_resolution_value(app_jsx_source)
    broken_client = MagicMock()
    broken_client.start_session.side_effect = RuntimeError("no database in this test")
    monkeypatch.setattr(main, "client", broken_client)  # prevents any real DB attempt once validation passes
    r = as_role("supervisor").post("/api/conflicts/CF-X/resolve", json={"resolution": sent, "selected_value": "v"})
    assert r.status_code != 400, f"{sent!r} -> {r.status_code} {r.json()}"


class _CommitFails:
    """Fake pymongo session whose transaction commit fails (control-flow simulation, NOT a real Mongo test)."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def start_transaction(self):
        class Tx:
            def __enter__(s): return s
            def __exit__(s, et, ev, tb):
                if et is None:
                    raise OperationFailure("simulated commit failure")
                return False
        return Tx()


@pytest.mark.xfail(strict=True, reason="S4: 'applied' result is reported even though the transaction commit failed")
def test_S4_failed_commit_never_reports_applied(as_role, monkeypatch):
    fake_client = MagicMock()
    fake_client.start_session.return_value = _CommitFails()
    fake_db = MagicMock()
    fake_db.operations.find_one.return_value = None
    fake_db.patients.find_one.return_value = None
    fake_db.counters.find_one_and_update.return_value = {"value": 1}
    monkeypatch.setattr(main, "client", fake_client)
    monkeypatch.setattr(main, "db", fake_db)
    r = as_role("worker").post("/api/sync/batch", json={"operations": [
        {"operation_id": "op-s4", "patient_id": "SH-1234", "device_id": "d1", "base_version": 0,
         "changes": {"name": "Test Person", "__create__": True}}]})
    results = r.json()["results"]
    statuses = [x["status"] for x in results if x["operation_id"] == "op-s4"]
    assert statuses == ["retryable_error"], statuses
