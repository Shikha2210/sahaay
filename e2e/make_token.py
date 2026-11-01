"""Test helper: sign a JWT with the backend's REAL secret, for the expired-session test. Prints only the token.
Usage: make_token.py expired <user_id> <role>"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
from jose import jwt  # noqa: E402
from app.config import settings  # noqa: E402

kind, uid, role = sys.argv[1:4]
now = datetime.now(timezone.utc)
if kind == "expired":
    iat, exp = now - timedelta(hours=2), now - timedelta(minutes=5)
else:
    raise SystemExit("unknown kind")
print(jwt.encode({"sub": uid, "role": role, "iat": iat, "exp": exp}, settings.jwt_secret, algorithm="HS256"))
