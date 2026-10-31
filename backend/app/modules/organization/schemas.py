"""Organization request and response shapes."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    legal_name: str
    short_name: str | None
    tax_number: str | None
    legal_form: str | None
    activity_description: str | None
    address: str | None
    phone: str | None
    email: EmailStr | None
    registered_on: date | None
    base_currency: str
    timezone: str
    default_locale: str
    is_active: bool


class OrganizationUpdateRequest(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    short_name: str | None = Field(default=None, max_length=128)
    tax_number: str | None = Field(default=None, pattern=r"^[0-9]{9,14}$")
    legal_form: str | None = Field(default=None, max_length=32)
    activity_description: str | None = None
    address: str | None = None
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    registered_on: date | None = None
    timezone: str | None = Field(default=None, max_length=64)
    default_locale: str | None = Field(default=None, pattern="^(uz|ru|en)$")


class OrganizationCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    legal_name: str = Field(min_length=2, max_length=255)
    short_name: str | None = Field(default=None, max_length=128)
    tax_number: str | None = Field(default=None, pattern=r"^[0-9]{9,14}$")
    legal_form: str | None = Field(default="MChJ", max_length=32)
    activity_description: str | None = None
    base_currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")
    timezone: str = Field(default="Asia/Tashkent", max_length=64)
    default_locale: str = Field(default="uz", pattern="^(uz|ru|en)$")
    registered_on: date | None = None

    #: Email of the person who will hold the primary owner membership.
    owner_email: EmailStr
    owner_full_name: str = Field(min_length=2, max_length=255)
    owner_password: str = Field(min_length=1, max_length=256)

    @field_validator("code")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class CurrencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name_en: str
    name_uz: str
    name_ru: str
    symbol: str | None
    decimal_places: int
    is_active: bool
