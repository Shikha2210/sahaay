import re
from pathlib import Path
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always backend/.env, no matter which folder the server is started from.
ENV_FILE = Path(__file__).resolve().parent.parent / '.env'

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
    # Where the backend sends AI requests. The API key never leaves the backend; this URL is server configuration only
    # (https, or http to this machine for local test doubles).
    groq_api_url: str = Field(default='https://api.groq.com/openai/v1/chat/completions', alias='GROQ_API_URL')
    ai_timeout_seconds: int = Field(default=20, ge=3, le=60, alias='AI_TIMEOUT_SECONDS')
    ai_rate_per_minute: int = Field(default=10, ge=1, le=120, alias='AI_RATE_PER_MINUTE')            # per signed-in user
    ai_global_per_minute: int = Field(default=30, ge=1, le=600, alias='AI_GLOBAL_PER_MINUTE')         # all users together (protects the shared key)
    bootstrap_supervisor_email: str = Field(default='supervisor@example.com', alias='BOOTSTRAP_SUPERVISOR_EMAIL')
    # No default: only needed when no supervisor exists yet. Validated when used (see bootstrap()).
    bootstrap_supervisor_password: str = Field(default='', alias='BOOTSTRAP_SUPERVISOR_PASSWORD')
    seed_demo_data: bool = Field(default=False, alias='SEED_DEMO_DATA')
    # true: a patient who signs up immediately gets their OWN new health record (no supervisor approval). They never get access to anyone else's.
    # false: the earlier mode where a supervisor must link the account to an existing record after an identity check.
    self_service_patient_records: bool = Field(default=True, alias='SELF_SERVICE_PATIENT_RECORDS')
    # false (default): sign-up never connects to an existing record (only a supervisor can do that). true: the older Patient ID + phone self-claim.
    allow_record_claim: bool = Field(default=False, alias='ALLOW_RECORD_CLAIM')
    # DEMO ONLY. true: anyone can create a health-worker account (email + password) with no approval, and so can read and edit every patient record in the
    # database. Refused unless MONGODB_DATABASE starts with 'sahaay_demo', so it can never be switched on for the real 'sahaay' database. Synthetic data only.
    allow_worker_signup: bool = Field(default=False, alias='ALLOW_WORKER_SIGNUP')
    # DEMO ONLY. true: anyone can create a health-worker account (email + password) with no approval, and so can read and edit every patient record in the
    # database. Refused unless MONGODB_DATABASE starts with 'sahaay_demo', so it can never be switched on for the real 'sahaay' database. Synthetic data only.
    allow_worker_signup: bool = Field(default=False, alias='ALLOW_WORKER_SIGNUP')
    # 'production' turns on the strict checks below (no API docs, HSTS, https-only origins, no demo data, no example accounts).
    environment: str = Field(default='development', alias='ENVIRONMENT')
    # Pull the real client address from X-Forwarded-For only when this is true (and only behind a proxy you control).
    trust_proxy_headers: bool = Field(default=False, alias='TRUST_PROXY_HEADERS')
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra='ignore', populate_by_name=True, hide_input_in_errors=True)   # a failed validation must never print the secret it rejected

    @field_validator('jwt_secret')
    @classmethod
    def _check_secret(cls, v):
        return validate_jwt_secret(v)

    @field_validator('groq_api_url')
    @classmethod
    def _check_ai_url(cls, v):
        if not (v.startswith('https://') or v.startswith('http://127.0.0.1') or v.startswith('http://localhost')):
            raise ValueError('GROQ_API_URL must be https:// (or http:// to localhost for a local test double)')
        return v

    @field_validator('cors_origins')
    @classmethod
    def _check_cors(cls, v):
        origins = [x.strip() for x in v.split(',') if x.strip()]
        if not origins or '*' in origins:
            raise ValueError('CORS_ORIGINS must list explicit origins; "*" is not allowed with credentials')
        return v


    @field_validator('environment')
    @classmethod
    def _check_env(cls, v):
        v = (v or 'development').strip().lower()
        if v not in ('development', 'production'):
            raise ValueError('ENVIRONMENT must be development or production')
        return v

    @model_validator(mode='after')
    def _production_rules(self):
        if self.allow_worker_signup and not self.mongodb_database.startswith('sahaay_demo'):
            raise ValueError('ALLOW_WORKER_SIGNUP is demo-only: MONGODB_DATABASE must start with sahaay_demo (synthetic data only)')
        if self.allow_worker_signup and not self.mongodb_database.startswith('sahaay_demo'):
            raise ValueError('ALLOW_WORKER_SIGNUP is demo-only: MONGODB_DATABASE must start with sahaay_demo (synthetic data only)')
        if self.environment != 'production': return self
        problems = []
        if self.seed_demo_data: problems.append('SEED_DEMO_DATA must be false in production')
        origins = [x.strip() for x in self.cors_origins.split(',') if x.strip()]
        if any(not o.startswith('https://') for o in origins): problems.append('CORS_ORIGINS must be https:// origins only in production (no localhost)')
        if self.bootstrap_supervisor_email.lower().endswith(('@example.com', '@example.org', '@test.com')) and self.bootstrap_supervisor_password:
            problems.append('BOOTSTRAP_SUPERVISOR_EMAIL must be a real address you control, not an example domain')
        if self.groq_api_url.startswith('http://'): problems.append('GROQ_API_URL must be https:// in production')
        if not self.mongodb_uri.startswith('mongodb+srv://') and 'tls=true' not in self.mongodb_uri.lower() and 'ssl=true' not in self.mongodb_uri.lower():
            problems.append('MONGODB_URI must use TLS (mongodb+srv:// or tls=true) in production')
        if self.mongodb_database.startswith('sahaay_test_'): problems.append('MONGODB_DATABASE is a test database name')
        if problems: raise ValueError('Unsafe production configuration: ' + '; '.join(problems))
        return self


settings = Settings()
