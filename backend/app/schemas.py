import re
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from typing import Any, Literal

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: str = 'patient'
    # Optional, patient sign-up only: connect to the record a health worker already created (needs BOTH to match).
    claim_patient_id: str | None = Field(default=None, max_length=40)
    claim_phone: str | None = Field(default=None, max_length=40)

class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)     # an email address, or a staff login ID such as HW-7K3Q9D
    password: str = Field(max_length=128)

class StaffIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=10, max_length=128)
    role: str = 'worker'
    email: EmailStr | None = None            # optional: staff can sign in with the login ID that is generated for them

class PatientIn(BaseModel):
    patient_id: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    age: int | None = Field(default=None, ge=0, le=120)
    phone: str | None = None
    village: str | None = None
    condition: str | None = None
    identity_status: str = 'UNVERIFIED'
    last_visit: str | None = None

# Per-field limits for values arriving through sync (applied to every field a client may send).
_STR_LIMITS = {'name': 120, 'phone': 40, 'village': 120, 'condition': 500, 'diagnosis': 500, 'medications': 1000,
               'allergies': 500, 'treatment_plan': 2000, 'blood_pressure': 20, 'blood_glucose': 20, 'dosage': 100,
               'last_visit': 60, 'identity_status': 30}


def validate_change_values(changes: dict) -> dict:
    """Reject nested objects, oversized strings and wrong types. Unknown field NAMES are handled by the sync route."""
    if len(changes) > 30:
        raise ValueError('too many fields in one operation')
    for k, v in changes.items():
        if k == '__create__':
            if not isinstance(v, bool): raise ValueError('__create__ must be a boolean')
        elif k == 'age':
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 120: raise ValueError('age must be an integer 0-120')
        elif k in _STR_LIMITS:
            if not isinstance(v, str) or len(v) > _STR_LIMITS[k]: raise ValueError(f'{k} must be text up to {_STR_LIMITS[k]} characters')
        elif isinstance(v, (dict, list)) or (isinstance(v, str) and len(v) > 500):
            raise ValueError(f'{k} has an unsupported value')  # unknown names are rejected later with a clear reason
    return changes


class OperationIn(BaseModel):
    # Shape only; content problems are reported per operation (status "rejected") so one bad
    # operation can never block the rest of a batch. See operation_problem().
    operation_id: str = Field(max_length=200)
    patient_id: str = Field(max_length=200)
    device_id: str = Field(max_length=200)
    base_version: int = Field(ge=0)
    changes: dict[str, Any]
    created_at: str | None = None
    schema_version: int = 1


_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]+$')


def operation_problem(op: OperationIn) -> str | None:
    """Return a short reason if the operation content is invalid, else None."""
    if not 8 <= len(op.operation_id) <= 64 or not _ID_RE.match(op.operation_id): return 'invalid_operation_id'
    if not 3 <= len(op.patient_id) <= 40 or not _ID_RE.match(op.patient_id): return 'invalid_patient_id'
    if not 3 <= len(op.device_id) <= 64: return 'invalid_device_id'
    try: validate_change_values(op.changes)
    except ValueError as exc: return f'invalid_value: {exc}'
    return None


class BatchIn(BaseModel):
    operations: list[OperationIn] = Field(max_length=100)

class ResolveIn(BaseModel):
    """Body of POST /api/conflicts/{id}/resolve. Which resolutions are allowed depends on the conflict category and the
    caller's role (see documentation/CONFLICT_POLICY.md). The VALUE of apply_evidence / apply_confirmation always comes
    from the stored approved evidence / patient confirmation, never from the caller."""
    model_config = ConfigDict(extra='forbid')
    resolution: str = Field(min_length=2, max_length=40)
    expected_field_version: int | None = Field(default=None, ge=0)   # the field version the reviewer was looking at
    selected_value: Any = None                                      # only for resolution == "custom"
    evidence_reference: str | None = Field(default=None, max_length=100)
    patient_confirmation_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class EvidenceSubmitIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(pattern=r'^[A-Za-z0-9_.:#-]{3,100}$')    # a reference to a document held elsewhere (no file upload)
    description: str = Field(min_length=3, max_length=1000)
    claimed_value: Any = Field(...)                                 # what the submitter says the document shows


class EvidenceReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approve', 'reject']
    review_note: str = Field(min_length=3, max_length=500)
    approved_value: Any = None                                      # REQUIRED when approving: the exact value the clinician certifies


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conflict_id: str = Field(min_length=3, max_length=64)
    confirmed_value: Any = Field(...)


class LinkAccountIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: EmailStr
    identity_verified: bool                                         # supervisor attests an out-of-band identity check was done
    identity_check_method: Literal['in_person_document', 'trusted_introduction', 'other']
    identity_check_note: str = Field(min_length=5, max_length=500)


class IdentityAttemptIn(BaseModel):
    model_config = ConfigDict(extra='forbid')  # raw QR/XML/Aadhaar fields are refused, not ignored
    consent: bool
    method: Literal['AADHAAR_SECURE_QR', 'OFFLINE_EKYC_XML']


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    text: str = Field(max_length=2000)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=2000)
    language: str = Field(default='auto', max_length=8)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)     # kept in the browser only; redacted again by the server

    @field_validator('message')
    @classmethod
    def _strip(cls, v):
        v = v.strip()
        if not v: raise ValueError('message must not be blank')
        return v


class SummaryIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    patient_id: str | None = Field(default=None, max_length=40)              # patients may omit it (their own record is used); clinicians must name one
    language: str = Field(default='auto', max_length=8)
    acknowledge_external_ai: bool = False                                    # explicit consent to send minimal, de-identified details to an external AI service


class ExplainIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conflict_id: str = Field(min_length=3, max_length=64)
    language: str = Field(default='auto', max_length=8)
