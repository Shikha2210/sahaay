from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Any

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

class OperationIn(BaseModel):
    operation_id: str
    patient_id: str
    device_id: str
    base_version: int = Field(ge=0)
    changes: dict[str, Any]
    created_at: str | None = None
    schema_version: int = 1

class BatchIn(BaseModel):
    operations: list[OperationIn] = Field(max_length=100)

class ResolveIn(BaseModel):
    resolution: str = Field(min_length=2, max_length=2000)
    selected_value: Any = None
    evidence_reference: str | None = None
    patient_confirmation_id: str | None = None
