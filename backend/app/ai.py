"""SAHAAY AI layer (Milestone 5): everything that stands between our users/data and an external AI service.

Principles (see documentation/MILESTONE_5_REPORT.md):
  * The Groq API key exists only here, on the backend. It is never returned, logged or sent to the browser.
  * Data minimisation: nothing is sent unless it is needed; identifiers (name, phone, address, patient id, Aadhaar-like numbers,
    credentials) are removed or never included; conflict explanations contain NO record values at all.
  * Nothing the user types or the AI answers is stored (no chat history in the database).
  * The AI only produces text. It has no tools and cannot approve, reject or resolve anything.
  * AI output is never presented as a diagnosis: prompts forbid it, a server-side guard replaces diagnostic / dosing language,
    emergencies are answered by a fixed message without calling the model.
"""
import re
import threading
import time

import httpx

from app.config import settings

LANGUAGES = {
    'auto': 'the same language the user wrote in (English if unclear)',
    'en': 'English', 'hi': 'Hindi (Devanagari script)', 'bn': 'Bengali', 'ta': 'Tamil', 'te': 'Telugu', 'mr': 'Marathi',
    'gu': 'Gujarati', 'kn': 'Kannada', 'ml': 'Malayalam', 'pa': 'Punjabi (Gurmukhi script)', 'ur': 'Urdu',
}
DISCLAIMER = 'AI-generated general information. It is not medical advice or a diagnosis. Ask a doctor, nurse or health worker about your own care.'


class AiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message); self.status, self.code, self.message, self.extra = status, code, message, extra


# ------------------------------------------------------------------------------------------------ privacy: redaction
_PATTERNS = [
    ('credentials', re.compile(r'\b(?:gsk_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})')),
    ('secret words', re.compile(r'(?i)\b(?:otp|password|passcode|pin|share code|cvv)\b\s*(?:is|:|=)?\s*\S+')),
    ('email address', re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')),
    ('Aadhaar-like number', re.compile(r'(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?:[\s-]?\d{4})?(?!\d)')),
    ('phone number', re.compile(r'(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)')),
    ('patient ID', re.compile(r'(?i)\bSH-[A-Za-z0-9]{3,}\b')),
    ('long number', re.compile(r'(?<!\d)\d{9,}(?!\d)')),
]


def redact(text):
    """Remove identifiers/credentials from free text. Returns (clean_text, {kind: count}). Names cannot be detected reliably;
    the UI tells users not to type them."""
    counts = {}
    for kind, rx in _PATTERNS:
        text, n = rx.subn(f'[{kind} removed]', text)
        if n: counts[kind] = counts.get(kind, 0) + n
    return text, counts


# ----------------------------------------------------------------------------------------------- safety: emergencies
_EMERGENCY = re.compile(
    r"(?i)(chest pain|can'?t breathe|cannot breathe|trouble breathing|difficulty breathing|not breathing|short(?:ness)? of breath"
    r"|unconscious|passed out|fainted|seizure|convulsion|stroke|face drooping|severe bleeding|heavy bleeding|bleeding (?:a lot|heavily)"
    r"|heart attack|overdose|poison|snake ?bite|suicid|kill myself|end my life|want to die|self[- ]harm|pregnan\w*.{0,30}bleed"
    r"|सीने में दर्द|साँस नहीं|सांस नहीं|सांस लेने में|बेहोश|दौरा|ज़हर|जहर|आत्महत्या|खून बहुत|ज्यादा खून)")
EMERGENCY_TEXT = (
    "This may be an emergency. Please call your local emergency number now (112 in India) or go to the nearest hospital straight away. "
    "If someone is with you, ask them to stay with you. I cannot assess emergencies or give treatment advice.\n\n"
    "यह आपातकालीन स्थिति हो सकती है। कृपया अभी अपने स्थानीय आपातकालीन नंबर (भारत में 112) पर कॉल करें या तुरंत नज़दीकी अस्पताल जाएँ। "
    "यदि कोई आपके साथ है तो उनसे साथ रहने को कहें। मैं आपातकाल का आकलन या इलाज की सलाह नहीं दे सकता।")


def is_emergency(text): return bool(_EMERGENCY.search(text or ''))


# -------------------------------------------------------------------------------------------- safety: output guard
_DIAGNOSIS = re.compile(r"(?i)\b(?:you (?:are|were) (?:diagnosed with|suffering from)|your diagnosis is|you (?:probably|likely|definitely|certainly) have|i (?:can )?diagnose you)\b")
_DOSING = re.compile(r"(?i)\b(?:take|use|give|inject|increase|decrease|reduce|double|stop)\b[^.\n]{0,40}?\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|g|ml|iu|units?)\b")
GUARD_TEXT = ("I can't give a diagnosis or tell anyone which medicine or dose to take. Please talk to a doctor, nurse or pharmacist about that. "
              "I'm happy to explain general health terms or help you prepare questions to ask.")


def guard_output(answer, strict=True):
    """Replace answers that read like a diagnosis or a dosing instruction. `strict=False` (record summaries) only checks diagnosis wording,
    because a summary may legitimately restate a dose that is written in the record."""
    if _DIAGNOSIS.search(answer) or (strict and _DOSING.search(answer)): return GUARD_TEXT, True
    return answer, False


# ------------------------------------------------------------------------------------------------------ rate limiting
class SlidingLimiter:
    def __init__(self, window=60):
        self.window, self._hits, self._lock = window, {}, threading.Lock()

    def hit(self, key, limit):
        """Record a request if allowed. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > now - self.window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False, max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now); self._hits[key] = hits
            return True, 0

    def reset(self):
        with self._lock: self._hits.clear()


limiter = SlidingLimiter()


def check_rate(user_id):
    ok, wait = limiter.hit('u:' + str(user_id), settings.ai_rate_per_minute)
    if not ok: raise AiError(429, 'ai_rate_limited', f'You are sending requests too quickly. Please wait {wait} seconds.', retry_after_seconds=wait, scope='user')
    ok, wait = limiter.hit('global', settings.ai_global_per_minute)
    if not ok: raise AiError(429, 'ai_rate_limited', f'The assistant is busy right now. Please try again in {wait} seconds.', retry_after_seconds=wait, scope='service')


# ------------------------------------------------------------------------------------------------------------- prompts
def language_line(lang):
    if lang == 'auto': return 'Reply in the same language the user wrote in (English if unclear).'
    return f'Reply entirely in {LANGUAGES[lang]}. Keep medical terms accurate; if you are not sure of a translation, add the English term in brackets.'


_COMMON_RULES = ("Never diagnose and never say what condition a person has. Never recommend, start, stop or change any medicine or dose. "
                 "Do not invent facts, statistics, studies or sources; if you are not sure, say so. "
                 "Never ask for or repeat names, phone numbers, Aadhaar or other ID numbers, passwords or OTPs. ")

CHAT_SYSTEM = ("You are SAHAAY, a general health-information assistant for community healthcare in India. You explain common health terms, general prevention "
               "and wellness, and help people prepare questions for a healthcare professional. " + _COMMON_RULES +
               "Do not interpret a person's own test results or record; explain in general what a term means and suggest asking their health worker or doctor. "
               "For symptoms that could be urgent (chest pain, trouble breathing, severe bleeding, fainting, stroke signs, suicidal thoughts, poisoning, pregnancy "
               "complications) tell the person to contact local emergency services (112 in India) or go to the nearest hospital now. "
               "Keep answers under 180 words, plain and kind. ")

SUMMARY_SYSTEM = ("You write a short plain-language summary of a health record for the {audience}. Use ONLY the facts listed in the user message. "
                  "If something is not listed, say it is not recorded - never fill gaps. " + _COMMON_RULES +
                  "Do not interpret values or say whether they are good or bad. Finish with two or three questions the {audience_short} could ask their care team. "
                  "Keep it under 200 words. ")

EXPLAIN_SYSTEM = ("You explain, in plain language, what a data conflict in a healthcare record system means and what happens next. A conflict means two devices recorded "
                  "different values for the same detail; the system keeps both and does not overwrite anything. Use ONLY the facts in the user message. "
                  "You are not told the actual values and must not guess them. Do NOT say which value is correct. Do NOT approve, reject or resolve anything and do not tell "
                  "anyone to change patient data; state clearly that only the people named in the facts can decide. " + _COMMON_RULES +
                  "Keep it under 150 words. ")


def build_chat_messages(message, history, lang):
    msgs = [{'role': 'system', 'content': CHAT_SYSTEM + language_line(lang)}]
    for turn in history[-6:]:
        text, _ = redact(turn['text'])
        msgs.append({'role': 'user' if turn['role'] == 'user' else 'assistant', 'content': text[:1500]})
    msgs.append({'role': 'user', 'content': message})
    return msgs


# ------------------------------------------------------------------------------------------------- minimal data builders
FIELD_LABEL = {'name': 'name', 'age': 'age', 'phone': 'phone number', 'village': 'village', 'condition': 'condition', 'diagnosis': 'diagnosis', 'medications': 'medications',
               'allergies': 'allergies', 'treatment_plan': 'treatment plan', 'blood_pressure': 'blood pressure', 'blood_glucose': 'blood glucose', 'dosage': 'dosage', 'last_visit': 'last visit date'}
SUMMARY_FIELDS = ['condition', 'diagnosis', 'medications', 'allergies', 'treatment_plan', 'blood_pressure', 'blood_glucose', 'dosage', 'last_visit']


def age_band(age):
    if isinstance(age, bool) or not isinstance(age, int) or age < 0: return None
    return 'under 18' if age < 18 else ('90 or over' if age >= 90 else f'{age // 10 * 10}-{age // 10 * 10 + 9}')


def minimal_record_facts(patient):
    """The ONLY patient data that may leave the server for a summary: an age band and clinical fields, with identifiers removed.
    Never included: name, phone, village, patient id, identity status, timestamps, who created/edited the record."""
    items, redactions = [], {}
    band = age_band(patient.get('age'))
    if band: items.append(('age range', band))
    for f in SUMMARY_FIELDS:
        v = patient.get(f)
        if v is None or (isinstance(v, str) and not v.strip()): continue
        clean, counts = redact(str(v)[:300])
        for k, n in counts.items(): redactions[k] = redactions.get(k, 0) + n
        items.append((FIELD_LABEL[f], clean))
    clinical = [i for i in items if i[0] != 'age range']
    return {'items': items, 'has_clinical': bool(clinical), 'fields_shared': [k for k, _ in items], 'redactions': redactions}


_KIND = {
    'DOCUMENT_VERIFIABLE': ('a detail that can be proven with a document, such as a name or age',
                            'a clinician reviews the supporting document reference and records the exact value it supports; then a supervisor or clinician applies that value'),
    'PATIENT_VERIFIABLE': ('a personal contact detail that the patient can confirm, such as a phone number or village',
                           'the linked patient confirms which recorded value is correct; then a supervisor or clinician applies the confirmed value'),
    'CRITICAL_CLINICAL': ('clinical information', 'only an authorized clinician decides'),
    'REQUIRES_REVIEW': ('a detail with no automatic verification route', 'only an authorized clinician decides'),
}


def conflict_facts(role, c, evidence, confirmations, changed):
    """Facts for a conflict explanation. NO record values, NO identifiers - only the shape of the situation and who may act."""
    kind, who = _KIND[c['category']]
    pend = c['status'] == 'pending'
    mine_ev = {s: sum(1 for e in evidence if e['status'] == s) for s in ('pending_review', 'approved', 'rejected')}
    confirmed = any(x['status'] == 'confirmed' for x in confirmations)
    if not pend: todo = f"This conflict is already {c['status']}; nothing further is needed."
    elif role == 'patient': todo = ('You have already confirmed a value; your care team will apply it.' if confirmed else
                                    'You can choose which of the two recorded values is correct on the "Confirm information" page. Your record does not change until staff apply your answer.')
    elif role == 'worker': todo = ('You can submit a reference to a supporting document; a clinician will review it.' if c['category'] == 'DOCUMENT_VERIFIABLE' else
                                   'Nothing is needed from you; the responsible reviewers will decide.')
    elif role == 'supervisor': todo = ('You can apply a clinician-approved value or the patient\'s confirmation once one exists.' if c['category'] in ('DOCUMENT_VERIFIABLE', 'PATIENT_VERIFIABLE') else
                                       'You cannot resolve this; only a clinician can. You can monitor it.')
    else: todo = 'You can review evidence and record a decision; the system will refuse a decision based on out-of-date information.'
    facts = {'detail_name': FIELD_LABEL.get(c['field'], 'a detail'), 'kind_of_detail': kind, 'how_it_is_decided': who, 'status': c['status'],
             'reader_role': role, 'what_the_reader_can_do': todo, 'changed_since_it_was_raised': bool(changed)}
    if c['category'] == 'DOCUMENT_VERIFIABLE': facts['evidence_records'] = mine_ev
    if c['category'] == 'PATIENT_VERIFIABLE': facts['patient_has_confirmed'] = confirmed
    return facts


# -------------------------------------------------------------------------------------------------- the external call
def configured(): return bool(settings.groq_api_key)


def groq_chat(messages, max_tokens=500):
    """The only place that talks to the external AI service. Raises AiError with a structured code on every failure. Never logs
    request or response text; never includes the key in any error."""
    if not configured(): raise AiError(503, 'ai_not_configured', 'The AI assistant is not set up on this server yet.')
    try:
        r = httpx.post(settings.groq_api_url, headers={'Authorization': f'Bearer {settings.groq_api_key}'},
                       json={'model': settings.groq_model, 'messages': messages, 'temperature': 0.2, 'max_tokens': max_tokens}, timeout=settings.ai_timeout_seconds)
    except httpx.TimeoutException: raise AiError(504, 'ai_timeout', 'The AI assistant took too long to answer. Please try again.')
    except httpx.HTTPError: raise AiError(502, 'ai_unavailable', 'The AI assistant could not be reached right now.')
    if r.status_code == 429:
        try: wait = max(1, int(float(r.headers.get('retry-after', '30'))))
        except ValueError: wait = 30
        raise AiError(429, 'ai_rate_limited', f'The AI service is busy. Please try again in {wait} seconds.', retry_after_seconds=wait, scope='provider')
    if r.status_code in (401, 403): raise AiError(502, 'ai_misconfigured', 'The AI service did not accept this server\'s credentials. Please tell an administrator.')
    if r.status_code >= 400: raise AiError(502, 'ai_unavailable', 'The AI assistant is temporarily unavailable.')
    try:
        answer = r.json()['choices'][0]['message']['content']
        if not isinstance(answer, str) or not answer.strip(): raise ValueError
    except (ValueError, KeyError, IndexError, TypeError): raise AiError(502, 'ai_bad_response', 'The AI assistant returned an unusable answer. Please try again.')
    return answer.strip()
