"""Organization and currency records.

An organization is the tenant boundary: its own ledger, chart of accounts,
periods, policies, employees and documents. Currency is global reference data
— the scale of UZS does not differ between two clinics.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Currency(Base, TimestampMixin):
    """A currency and the rounding scale that applies to it.

    `decimal_places` is the authority for quantizing an amount denominated in
    this currency; `app.core.money` falls back to a built-in table only before
    a row has been loaded.
    """

    __tablename__ = "currency"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(64), nullable=False)
    name_uz: Mapped[str] = mapped_column(String(64), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(8))
    decimal_places: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(
            "decimal_places BETWEEN 0 AND 6",
            name="decimal_places_in_range",
        ),
        CheckConstraint("code = upper(code)", name="code_uppercase"),
    )


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A legal entity whose books this platform keeps."""

    __tablename__ = "organization"

    #: Short stable handle used in URLs and document numbering prefixes.
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(128))
    #: Uzbek taxpayer identification number (STIR / INN), 9 digits.
    tax_number: Mapped[str | None] = mapped_column(String(20), index=True)
    legal_form: Mapped[str | None] = mapped_column(String(32))
    activity_description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    registered_on: Mapped[date | None] = mapped_column(Date)

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    #: IANA zone. Business dates (a cash session day, a receipt date) are
    #: derived in this zone; stored instants stay UTC.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Tashkent")
    default_locale: Mapped[str] = mapped_column(String(8), nullable=False, default="uz")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("char_length(code) >= 2", name="code_min_length"),
        CheckConstraint("base_currency = upper(base_currency)", name="base_currency_uppercase"),
        CheckConstraint(
            "tax_number IS NULL OR tax_number ~ '^[0-9]{9,14}$'",
            name="tax_number_digits",
        ),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.short_name or self.legal_name


__all__ = ["Currency", "Organization", "uuid"]
