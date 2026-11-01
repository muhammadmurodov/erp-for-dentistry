"""Request and response shapes for identity endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    #: Which organization to act in. Optional when the user has exactly one
    #: membership; required when they have several.
    organization_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower().strip()


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class SwitchOrganizationRequest(BaseModel):
    organization_id: uuid.UUID
    #: The caller's current refresh token, consumed and replaced.
    refresh_token: str = Field(min_length=16, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime
    must_change_password: bool = False


class MembershipSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_code: str
    job_title: str | None = None
    is_primary_owner: bool
    role_codes: list[str]


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    locale: str
    is_superuser: bool
    must_change_password: bool
    #: The membership this access token is bound to.
    active_membership: MembershipSummary | None
    #: Every organization this user may switch into.
    memberships: list[MembershipSummary]
    #: Resolved permission codes for the active membership.
    permissions: list[str]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    phone: str | None = Field(default=None, max_length=32)
    locale: str = Field(default="uz", pattern="^(uz|ru|en)$")
    role_codes: list[str] = Field(default_factory=list)
    job_title: str | None = Field(default=None, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower().strip()


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: str | None
    locale: str
    is_active: bool
    last_login_at: datetime | None


class OrganizationUserResponse(UserResponse):
    membership_id: uuid.UUID
    job_title: str | None
    membership_active: bool
    is_primary_owner: bool
    role_codes: list[str]


class RoleGrantRequest(BaseModel):
    role_codes: list[str] = Field(min_length=0, max_length=20)


class PermissionResponse(BaseModel):
    code: str
    module: str
    description: str
    is_sensitive: bool


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name_uz: str
    name_ru: str
    name_en: str
    description: str | None
    is_system: bool
    is_read_only: bool
    #: False for a role this organization has no use for — a middle-management
    #: role in a three-person clinic. Still grantable over the API; the
    #: assignment screen simply does not offer it, so the owner is never
    #: shown a choice whose only effect is a refusal (ADR-041).
    offered_for_assignment: bool = True
    permission_codes: list[str]
