"""Reproduction tests for bugs that are STILL OPEN (documentation/BUGS_AND_GAPS.md).

Each test asserts the CORRECT behaviour and is marked xfail(strict=True): while the bug exists it fails
("xfailed" = bug reproduced); once fixed it passes and strict-xfail turns that into a failure, telling us to
remove the marker.

Fixed in Milestone 2b and therefore moved to real regression tests (tests/test_sync_fixes.py):
S1 (offline create rejected), S2 (server side ordering), S3 (false self-conflicts), S4 (failed commit reported as applied).
"""
import re
from unittest.mock import MagicMock
import pytest
from app import main


def frontend_resolution_value(src):
    m = re.search(r"let payload=\{resolution:'([^']*)'", src)
    assert m, "could not find the resolve payload in App.jsx"
    return m.group(1)


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
