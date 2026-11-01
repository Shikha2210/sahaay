"""Frontend <-> backend contract for the AI features (Milestone 5): the browser only talks to our own routes, no key or Groq address is in
any frontend file or in the built bundle, no browser dialogs, no scripted fake answers, no personalised prompts."""
import re
import pytest
from app import main
from tests.conftest import REPO_ROOT

SRC = REPO_ROOT / "frontend" / "src"
DIST = REPO_ROOT / "frontend" / "dist"
ROUTES = {(r.path, m) for r in main.app.routes if hasattr(r, "methods") for m in r.methods}
AI_FILES = ["aiClient.js", "AiAssistant.jsx", "AiParts.jsx"]


def test_every_ai_route_the_client_uses_exists_with_the_right_method():
    for method, path in [("POST", "/api/chatbot/message"), ("POST", "/api/ai/patient-summary"), ("POST", "/api/ai/conflict-explanation"), ("GET", "/api/ai/status")]:
        assert (path, method) in ROUTES
    client = (SRC / "aiClient.js").read_text(encoding="utf-8")
    for lit in re.findall(r"['\"`](/api/[A-Za-z0-9_/\-]*)", client):
        assert any(p == lit.rstrip("/") for p, _ in ROUTES), lit


@pytest.mark.parametrize("name", AI_FILES)
def test_no_ai_secret_or_provider_address_in_frontend_source(name):
    src = (SRC / name).read_text(encoding="utf-8")
    for bad in ("api.groq.com", "gsk_", "GROQ_API_KEY", "Bearer ", "prompt(", "alert(", "window.confirm("):
        assert bad not in src, f"{name} contains {bad!r}"


def test_whole_frontend_source_never_mentions_the_ai_provider_key_or_endpoint():
    for f in SRC.rglob("*"):
        if f.suffix in (".js", ".jsx") and ".test." not in f.name:
            text = f.read_text(encoding="utf-8")
            assert "api.groq.com" not in text and "gsk_" not in text and "GROQ_API_KEY" not in text, f.name


@pytest.mark.skipif(not DIST.exists(), reason="run `npm run build` first")
def test_built_bundle_contains_no_key_or_provider_address():
    for f in DIST.rglob("*.js"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert "api.groq.com" not in text and "gsk_" not in text and "GROQ_API_KEY" not in text, f.name


def test_scripted_fake_assistant_replies_are_gone():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    assert "aiReply" not in app and "scripted" not in app.lower() and "api('/api/chatbot" not in app


def test_assistant_availability_badge_is_driven_by_the_server_status_not_hard_coded():
    page = (SRC / "AiAssistant.jsx").read_text(encoding="utf-8")
    assert "aiStatus()" in page and "status.configured" in page and "navigator.onLine" in page
    assert 'Badge tone="green">Assistant available' in page and page.count("Assistant available") == 1   # only inside the configured branch


def test_quick_prompts_do_not_invite_personal_medical_questions():
    page = (SRC / "AiAssistant.jsx").read_text(encoding="utf-8")
    prompts = page[page.index("const PROMPTS"): page.index("\n", page.index("const PROMPTS"))].lower()
    for personal in (" my blood pressure", "my reading", "my result", "my medicine", "my dose", "my record", "my phone"):
        assert personal not in prompts


def test_ai_text_is_rendered_as_text_never_as_html():
    for name in AI_FILES:
        src = (SRC / name).read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src


def test_consent_gate_and_privacy_notes_are_present_in_the_ui():
    parts = (SRC / "AiParts.jsx").read_text(encoding="utf-8")
    assert "disabled={!agree || busy}" in parts and "never sends" in parts.lower() and "recorded in the audit log" in parts
    assert "cannot approve, reject or resolve" in parts
    chat = (SRC / "AiAssistant.jsx").read_text(encoding="utf-8")
    assert "removed before it is sent" in chat and "not saved" in chat


def test_dialogs_taller_than_the_screen_can_scroll_so_confirmation_buttons_stay_reachable():
    """Regression (found in Milestone 5): the AI sections made the conflict dialog taller than a laptop screen and, because the shared
    .modal had no max-height/overflow, its confirmation buttons ended up off-screen and unreachable."""
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"\.modal\{([^}]*)\}", css).group(1)
    assert "max-height" in rule and "overflow-y:auto" in rule.replace(" ", "")
