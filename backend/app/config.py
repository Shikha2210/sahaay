import re
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that must never be accepted as secrets/passwords (placeholders and the old built-in default).
_PLACEHOLDER_MARKERS = ('replace', 'changeme', 'change_me', 'changethis', 'example', 'your_secret', 'yoursecret',
                        'password', 'secret_key', 'jwt_secret', 'sahaay')
KNOWN_BAD_PASSWORDS = {'changethisbeforeuse123!'}


def validate_jwt_secret(value: str) -> str:
    """JWT signing key must be explicitly configured, long and not a placeholder."""
    v = (value or '').strip()
    if not v:
        raise ValueError('JWT_SECRET is required. Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"')
    if len(v) < 32:
        raise ValueError('JWT_SECRET is too short: use at least 32 characters (48+ recommended)')
    low = v.lower()
    if any(m in low for m in _PLACEHOLDER_MARKERS):
        raise ValueError('JWT_SECRET looks like a placeholder/guessable text; generate a random one')
    if len(set(v)) < 12:
        raise ValueError('JWT_SECRET has too little variety; generate a random one')
    return v


def validate_bootstrap_email(value: str) -> str:
    """Must pass the same validation the login form uses, otherwise the account could never sign in."""
    from email_validator import validate_email, EmailNotValidError
    try: return validate_email((value or '').strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError(f'BOOTSTRAP_SUPERVISOR_EMAIL is not a usable email address: {exc}') from exc


def validate_bootstrap_password(value: str, email: str = '') -> str:
    """Password for the first supervisor account. No default exists; it must be chosen by the operator."""
    v = value or ''
    if len(v) < 12:
        raise ValueError('BOOTSTRAP_SUPERVISOR_PASSWORD must be at least 12 characters')
    if v.lower() in KNOWN_BAD_PASSWORDS:
        raise ValueError('BOOTSTRAP_SUPERVISOR_PASSWORD is a known default; choose your own')
    if not (re.search(r'[a-z]', v) and re.search(r'[A-Z]', v) and re.search(r'\d', v) and re.search(r'[^A-Za-z0-9]', v)):
        raise ValueError('BOOTSTRAP_SUPERVISOR_PASSWORD needs upper-case, lower-case, a digit and a symbol')
    low = v.lower()
    if any(m in low for m in ('password', 'sahaay', 'qwerty', '12345')):
        raise ValueError('BOOTSTRAP_SUPERVISOR_PASSWORD contains a guessable word/pattern')
    local = email.split('@')[0].lower() if email else ''
    if local and len(local) >= 4 and local in low:
        raise ValueError('BOOTSTRAP_SUPERVISOR_PASSWORD must not contain the account email name')
    return v


class Settings(BaseSettings):
    mongodb_uri: str = Field(alias='MONGODB_URI')
    mongodb_database: str = Field(default='sahaay', alias='MONGODB_DATABASE')
    jwt_secret: str = Field(alias='JWT_SECRET')
    # Short-lived access tokens (was 720). Expiry is handled cleanly by the client (re-login keeps local data).
    jwt_expire_minutes: int = Field(default=60, ge=5, le=240, alias='JWT_EXPIRE_MINUTES')
    cors_origins: str = Field(default='http://localhost:5173,http://127.0.0.1:5173', alias='CORS_ORIGINS')
    groq_api_key: str = Field(default='', alias='GROQ_API_KEY')
    groq_model: str = Field(default='llama-3.3-70b-versatile', alias='GROQ_MODEL')
    bootstrap_supervisor_email: str = Field(default='supervisor@example.com', alias='BOOTSTRAP_SUPERVISOR_EMAIL')
    # No default: only needed when no supervisor exists yet. Validated when used (see bootstrap()).
    bootstrap_supervisor_password: str = Field(default='', alias='BOOTSTRAP_SUPERVISOR_PASSWORD')
    seed_demo_data: bool = Field(default=False, alias='SEED_DEMO_DATA')
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', populate_by_name=True)

    @field_validator('jwt_secret')
    @classmethod
    def _check_secret(cls, v):
        return validate_jwt_secret(v)

    @field_validator('cors_origins')
    @classmethod
    def _check_cors(cls, v):
        origins = [x.strip() for x in v.split(',') if x.strip()]
        if not origins or '*' in origins:
            raise ValueError('CORS_ORIGINS must list explicit origins; "*" is not allowed with credentials')
        return v


settings = Settings()
