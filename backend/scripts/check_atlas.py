"""Read-only Atlas connectivity check using the backend's own configuration.

* Loads backend/.env through app.config (so JWT_SECRET etc. are validated exactly as the server will).
* Sends ONE `ping` and a `hello`. It creates nothing, changes nothing, deletes nothing, creates no indexes.
* Never prints the connection string, username, password or secrets (output is also scrubbed).

Run from anywhere:   backend\\.venv\\Scripts\\python.exe backend\\scripts\\check_atlas.py
"""
import sys
import pathlib
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def scrub(text, secrets):
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text


def main():
    env_file = pathlib.Path(__file__).resolve().parents[1] / '.env'
    if not env_file.exists():
        print(f"FAIL  {env_file} does not exist. Run: powershell -File scripts\\setup_env.ps1")
        return 2
    try:
        from app.config import settings, validate_bootstrap_email, validate_bootstrap_password
    except Exception as exc:  # pydantic ValidationError: names the field, never echoes the value
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in getattr(exc, "errors", lambda: [])()})
        print("FAIL  .env is invalid for field(s):", ", ".join(fields) or type(exc).__name__)
        for e in getattr(exc, "errors", lambda: [])():
            print("      -", ".".join(str(p) for p in e["loc"]), ":", e["msg"].split("\n")[0])
        return 2

    secrets = [settings.mongodb_uri, settings.jwt_secret, settings.bootstrap_supervisor_password, settings.groq_api_key]
    uri = settings.mongodb_uri
    parts = urlsplit(uri)
    userinfo = parts.netloc.rpartition("@")[0]
    if userinfo:
        secrets += [userinfo, userinfo.partition(":")[2]]
    host = parts.netloc.rpartition("@")[2]
    print("Configuration read from backend/.env (values hidden):")
    print(f"  MONGODB_URI ........ set  (scheme {parts.scheme}, host {host})")
    print(f"  MONGODB_DATABASE ... {settings.mongodb_database}")
    print("  JWT_SECRET ......... set and passes strength checks")
    print(f"  JWT_EXPIRE_MINUTES . {settings.jwt_expire_minutes}")
    print(f"  SEED_DEMO_DATA ..... {settings.seed_demo_data}")
    print(f"  GROQ_API_KEY ....... {'set' if settings.groq_api_key else 'empty (fine until the AI milestone)'}")
    try:
        validate_bootstrap_email(settings.bootstrap_supervisor_email)
        print(f"  BOOTSTRAP email .... {settings.bootstrap_supervisor_email}")
    except ValueError as exc:
        print("  BOOTSTRAP email .... INVALID:", exc)
    try:
        validate_bootstrap_password(settings.bootstrap_supervisor_password, settings.bootstrap_supervisor_email)
        print("  BOOTSTRAP password . set and passes checks")
    except ValueError as exc:
        print("  BOOTSTRAP password . NOT USABLE:", exc, "(re-run setup_env.ps1 -Redo BOOTSTRAP_SUPERVISOR_PASSWORD)")

    from pymongo import MongoClient
    from pymongo.server_api import ServerApi
    client = MongoClient(uri, server_api=ServerApi("1"), serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
        hello = client.admin.command("hello")
    except Exception as exc:
        msg = scrub(f"{type(exc).__name__}: {exc}", secrets)
        print("\nFAIL  Could not ping Atlas.")
        print("      " + msg[:400])
        low = msg.lower()
        if "authentication failed" in low or "bad auth" in low:
            print("      Hint: wrong database-user name/password (re-enter with -Redo MONGODB_URI). Special characters in the password must be URL-encoded.")
        elif "timeout" in low or "serverselection" in low or "ssl" in low or "dns" in low:
            print("      Hint: Atlas > Network Access: add your current IP address. Also check the cluster is not paused and that you have internet.")
        return 1
    finally:
        client.close()
    print("\nPASS  Atlas ping succeeded using the backend configuration.")
    is_rs = bool(hello.get("setName"))
    print(f"      Replica set: {'yes (' + hello['setName'] + ')' if is_rs else 'NO'}  -> MongoDB transactions {'are' if is_rs else 'are NOT'} supported")
    print("      (read-only check: nothing was created, changed or deleted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
