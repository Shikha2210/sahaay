"""Smoke test for the REAL Groq connection, using the backend's own configuration (backend/.env).

Sends ONE generic question that contains no patient data, and prints only the outcome. It never prints the API key, the
connection string or any setting values. Run after adding GROQ_API_KEY to backend/.env:

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\check_groq.py [language-code]      e.g.  ... check_groq.py hi
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    if lang not in ai.LANGUAGES:
        print("Unknown language code. Choose one of:", ", ".join(sorted(ai.LANGUAGES))); return 2
    print(f"GROQ_API_KEY configured: {'yes' if ai.configured() else 'NO'}   model: {settings.groq_model}   language: {lang}")
    if not ai.configured():
        print("FAIL  No key is set. Add GROQ_API_KEY=... to backend/.env (never commit that file), then run this again."); return 1
    try:
        answer = ai.groq_chat(ai.build_chat_messages("In two short sentences, what does the word 'anemia' mean?", [], lang), max_tokens=200)
    except ai.AiError as e:
        print(f"FAIL  {e.code}: {e.message}"); return 1
    guarded, flagged = ai.guard_output(answer)
    print("PASS  The real service answered through the backend's safety layer.")
    print("Answer:", guarded[:600].replace("\n", " "))
    print("Output guard replaced the answer:", flagged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
