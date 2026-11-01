"""Frontend <-> backend contract for the role workspaces (Milestone 4): every API path the new screens call must exist on the
server, with the HTTP method the screen uses, and the screens must not contain leftover demo content or browser dialogs."""
import re
import pytest
from app import main
from tests.conftest import REPO_ROOT

SRC = REPO_ROOT / "frontend" / "src"
ROUTES = {(r.path, m) for r in main.app.routes if hasattr(r, "methods") for m in r.methods}
PATHS = {p for p, _ in ROUTES}

EXPECTED = {
    "PatientWorkspace.jsx": [("GET", "/api/me/record"), ("GET", "/api/me/conflicts"), ("POST", "/api/patients/{patient_id}/confirm-field")],
    "WorkerConflicts.jsx": [("GET", "/api/sync/conflicts"), ("POST", "/api/conflicts/{conflict_id}/evidence")],
    "SupervisorAccounts.jsx": [("GET", "/api/admin/patient-accounts"), ("POST", "/api/admin/patients/{patient_id}/link-account")],
    "ClinicianWorkspace.jsx": [("GET", "/api/evidence"), ("GET", "/api/conflicts")],
}


@pytest.mark.parametrize("name,endpoints", EXPECTED.items())
def test_each_workspace_uses_endpoints_that_really_exist(name, endpoints):
    for method, path in endpoints:
        assert (path, method) in ROUTES, f"{name} needs {method} {path}, which the server does not have"


@pytest.mark.parametrize("name", list(EXPECTED))
def test_every_api_literal_in_a_workspace_is_a_real_route_prefix(name):
    src = (SRC / name).read_text(encoding="utf-8")
    literals = set(re.findall(r"['\"`](/api/[A-Za-z0-9_/\-]*)", src))
    assert literals, f"no API calls found in {name}"
    for lit in literals:
        base = lit.rstrip("/")
        assert any(p == base or p.startswith(base + "/") for p in PATHS), f"{name} calls {lit}, which is not an API route"


@pytest.mark.parametrize("name", list(EXPECTED) + ["ConflictReview.jsx"])
def test_workspaces_have_no_browser_dialogs_and_no_demo_patient_content(name):
    src = (SRC / name).read_text(encoding="utf-8")
    for bad in ("prompt(", "alert(", "window.confirm(", "Asha Devi", "Ravi Kumar", "Meena Sharma", "SH-2041", "On track", "04 Nov", "Demo activity"):
        assert bad not in src, f"{name} contains {bad!r}"


def test_app_no_longer_hard_codes_patient_or_worker_demo_numbers():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    for bad in ("Asha Devi", "Synthetic patient example", "On track", "value=\"2 pending\"", "value=\"08\"", "Demo activity", "value=\"02\"", "Demo pending", "synthetic demo data, not a real Aadhaar"):
        assert bad not in app, f"App.jsx still contains hard-coded demo content: {bad!r}"


def test_navigation_exposes_each_new_screen_to_the_right_role_only():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    nav = app[app.index("let nav="): app.index("\n", app.index("let nav="))]
    patient, rest = nav.split(":role==='worker'?", 1)
    worker, rest = rest.split(":role==='clinician'?", 1)
    clinician, supervisor = rest.split(":[['dashboard'", 1)
    assert "'confirmations'" in patient and "'confirmations'" not in worker + clinician + supervisor
    assert "'my-conflicts'" in worker and "'my-conflicts'" not in patient + clinician + supervisor
    assert "'evidence'" in clinician and "'history'" in clinician and "'accounts'" not in clinician
    assert "'accounts'" in supervisor and "'accounts'" not in patient + worker
