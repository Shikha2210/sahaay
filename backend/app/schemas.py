import re
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from typing import Any, Literal

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: str = 'patient'

class LoginIn(BaseModel):
    email: EmailStr
    password: str

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
    resolution: str = Field(min_length=2, max_length=2000)
    selected_value: Any = None
    evidence_reference: str | None = None
    patient_confirmation_id: str | None = None


class IdentityAttemptIn(BaseModel):
    model_config = ConfigDict(extra='forbid')  # raw QR/XML/Aadhaar fields are refused, not ignored
    consent: bool
    method: Literal['AADHAAR_SECURE_QR', 'OFFLINE_EKYC_XML']


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator('message')
    @classmethod
    def _strip(cls, v):
        v = v.strip()
        if not v: raise ValueError('message must not be blank')
        return v
