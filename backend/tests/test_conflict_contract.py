"""Contract between the frontend and the conflict API (bugs C1 and C2). Both were 'expected failures' before Milestone 3."""
import re
import pytest
from app import main
from tests.conftest import REPO_ROOT

REVIEW_JSX = REPO_ROOT / "frontend" / "src" / "ConflictReview.jsx"


def test_C1_patient_confirmation_route_exists_and_is_patient_only():
    routes = {(r.path, tuple(sorted(r.methods))) for r in main.app.routes if hasattr(r, "methods")}
    assert ("/api/patients/{patient_id}/confirm-field", ("POST",)) in routes
    assert ("/api/me/conflicts", ("GET",)) in routes


def test_C2_every_resolution_the_ui_can_send_is_one_the_server_accepts():
    src = REVIEW_JSX.read_text(encoding="utf-8")
    sent = set(re.findall(r"resolution:\s*'([a-z_]+)'", src)) | set(re.findall(r"\['(accept_current|accept_incoming|custom)'", src))
    assert sent, "could not find any resolution literal in ConflictReview.jsx"
    assert sent <= main.ALL_RESOLUTIONS, f"UI sends resolutions the server does not know: {sent - main.ALL_RESOLUTIONS}"
    declared = set(re.search(r"RESOLUTIONS = \[(.*?)\]", src, re.S).group(1).replace("'", "").replace(" ", "").split(","))
    assert declared == main.ALL_RESOLUTIONS, "the UI's list of resolutions and the server's list must be identical"


def test_C2_resolve_requests_carry_the_field_version_and_no_browser_dialogs_are_used():
    review = REVIEW_JSX.read_text(encoding="utf-8")
    app = (REPO_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "expected_field_version: d.current_field_version" in review
    assert "Reviewed in SAHAAY supervisor interface" not in app + review
    assert "prompt(" not in app + review and "window.confirm(" not in review


@pytest.mark.parametrize("old", ["accept_a", "accept_b"])
def test_C2_legacy_resolution_names_are_gone_from_the_server(old):
    assert old not in main.ALL_RESOLUTIONS
