"""TEST DOUBLE of the external Groq chat-completions API (the real service is never contacted: no Groq key exists in this project).
Runs on 127.0.0.1:8765 and records every request it receives so tests can prove exactly what the backend would have sent to Groq.
Every answer starts with "TEST-DOUBLE". Triggers (in the user's message): [[429]] rate limit with Retry-After 6, [[500]] server error,
[[slow]] 4-second delay, [[bad]] unusable body, [[diagnose]] / [[dose]] clinical-advice wording (to exercise the server-side guard)."""
import json
import pathlib
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = pathlib.Path(__file__).resolve().parent / "results" / "fake_groq_requests.jsonl"
LOG.parent.mkdir(exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, status, body, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        msgs = body.get("messages", []); system = msgs[0]["content"] if msgs else ""; user = msgs[-1]["content"] if msgs else ""
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "auth_prefix": self.headers.get("authorization", "")[:15], "model": body.get("model"), "messages": msgs}, ensure_ascii=False) + "\n")
        if "[[429]]" in user: return self._send(429, {"error": "rate limited"}, {"Retry-After": "6"})
        if "[[500]]" in user: return self._send(500, {"error": "boom"})
        if "[[bad]]" in user: return self._send(200, b"this is not json")
        if "[[slow]]" in user: time.sleep(4)
        m = re.search(r"Reply entirely in ([^.]+?)\.", system); lang = m.group(1) if m else "auto"
        if "[[diagnose]]" in user: text = "You probably have diabetes."
        elif "[[dose]]" in user: text = "You should take 500 mg twice a day."
        elif "plain-language summary" in system: text = f"TEST-DOUBLE SUMMARY [{lang}] - the record lists: " + "; ".join(l[2:] for l in user.splitlines() if l.startswith("- "))
        elif "data conflict" in system:
            d = re.search(r"- detail_name: (.+)", user); text = f"TEST-DOUBLE EXPLANATION [{lang}] - this is about the {d.group(1) if d else 'detail'}; only the named people can decide."
        else: text = f"TEST-DOUBLE ANSWER [{lang}] to: {user[:60]}"
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
