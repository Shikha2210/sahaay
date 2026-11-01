"""Shared helpers for the E2E scripts. Reads backend/.env at run time; NEVER prints secrets.
Run with the backend venv:  backend\\.venv\\Scripts\\python.exe e2e\\phaseN_*.py   (backend must be running on :8000)"""
import json
import pathlib
import secrets
import sys

import httpx
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.server_api import ServerApi

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / "backend" / ".env")
API = "http://127.0.0.1:8000"
SYNTH = "ZZ-SYNTHETIC"          # every record created by these scripts is labelled with this prefix
STATE_FILE = pathlib.Path(__file__).resolve().parent / ".e2e_state.json"   # git-ignored (holds synthetic passwords)


def mongo():
    c = MongoClient(ENV["MONGODB_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000)
    return c, c[ENV["MONGODB_DATABASE"]]


def http():
    return httpx.Client(base_url=API, timeout=30)


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(d):
    s = load_state(); s.update(d); STATE_FILE.write_text(json.dumps(s))


class Report:
    def __init__(self, name): self.name, self.rows = name, []

    def check(self, label, ok, detail=""):
        self.rows.append(("PASS" if ok else "FAIL", label, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
        return ok

    def note(self, status, label, detail=""):
        self.rows.append((status, label, detail)); print(f"{status:<5} {label}" + (f"  -- {detail}" if detail else ""))

    def finish(self):
        out = pathlib.Path(__file__).resolve().parent / "results"
        out.mkdir(exist_ok=True)
        (out / f"{self.name}.json").write_text(json.dumps(self.rows, indent=1))
        p = sum(r[0] == "PASS" for r in self.rows); f = sum(r[0] == "FAIL" for r in self.rows)
        print(f"\n{self.name}: {p} PASS, {f} FAIL, {len(self.rows)-p-f} other")


def strong_password():
    return "Syn-" + secrets.token_urlsafe(9) + "!aZ9"


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def auth(token): return {"Authorization": f"Bearer {token}"}
