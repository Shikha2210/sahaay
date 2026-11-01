import time
import threading
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from bson.errors import InvalidId
import jwt                                  # PyJWT (replaces python-jose, which pulled in unmaintained crypto dependencies)
from jwt import PyJWTError as JWTError, ExpiredSignatureError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from app.config import settings
from app.db import db

pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth = OAuth2PasswordBearer(tokenUrl='/api/auth/login')
ALGORITHM = 'HS256'


def hash_password(value: str) -> str: return pwd.hash(value)
def verify_password(value: str, hashed: str) -> bool: return pwd.verify(value, hashed)


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {'sub': str(user['_id']), 'role': user['role'], 'iat': now,
               'exp': now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def public_user(user: dict) -> dict:
    """The only user fields ever sent to the browser (never the password hash)."""
    return {'id': str(user['_id']), 'name': user['name'], 'email': user['email'], 'role': user['role'],
            'linked_patient_id': user.get('linked_patient_id')}


def _unauthorized(code: str, message: str):
    # `code` lets the frontend tell "session expired" (keep local data, ask to sign in) from other failures.
    return HTTPException(401, {'code': code, 'message': message}, headers={'WWW-Authenticate': 'Bearer'})


def current_user(token: str = Depends(oauth)):
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={'require': ['exp', 'sub']})
    except ExpiredSignatureError:
        raise _unauthorized('token_expired', 'Your session has expired. Please sign in again.')
    except JWTError:
        raise _unauthorized('token_invalid', 'Invalid access token')
    try:
        uid = ObjectId(payload['sub'])
    except (InvalidId, TypeError):
        raise _unauthorized('token_invalid', 'Invalid access token')
    user = db.users.find_one({'_id': uid, 'active': True})
    if not user:
        raise _unauthorized('account_unavailable', 'Account is unavailable')
    return user


def require_roles(*roles):
    def dependency(user=Depends(current_user)):
        if user['role'] not in roles:
            raise HTTPException(403, {'code': 'role_not_permitted', 'message': 'Your role is not permitted to do this.', 'required_roles': sorted(roles)})
        return user
    return dependency


class LoginRateLimiter:
    """In-memory failed-login limiter (per process). Good enough for one backend instance/demo;
    a multi-instance deployment needs a shared store (documented limitation)."""
    def __init__(self, max_failures=5, window_seconds=300):
        self.max, self.window = max_failures, window_seconds
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key):
        cutoff = time.monotonic() - self.window
        self._fails[key] = [t for t in self._fails.get(key, []) if t > cutoff]
        return self._fails[key]

    def blocked(self, key: str) -> bool:
        with self._lock: return len(self._recent(key)) >= self.max

    def record_failure(self, key: str):
        with self._lock: self._recent(key).append(time.monotonic())

    def reset(self, key: str):
        with self._lock: self._fails.pop(key, None)


login_limiter = LoginRateLimiter()                                         # per address + account
ip_login_limiter = LoginRateLimiter(max_failures=30, window_seconds=300)   # per address, across accounts (password spraying)
register_limiter = LoginRateLimiter(max_failures=10, window_seconds=3600)  # self-registrations per address per hour
