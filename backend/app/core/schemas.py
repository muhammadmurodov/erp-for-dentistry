"""Shared Pydantic building blocks."""

from __future__ import annotations

from decimal import Decimal

from pydantic import field_serializer

from app.core.money import for_display


class MoneyMixin:
    """Serialises every `Decimal` field as an exact decimal string.

    Two things happen here, and both matter:

    * **String, not JSON number.** A JSON number is an IEEE-754 double in
      every browser, and a large som amount would lose precision on the way
      out. The frontend formats the string and never converts it.
    * **Presented at the currency's scale.** Columns are `NUMERIC(20, 4)` for
      intermediate headroom; `for_display` strips that padding losslessly so
      an amount does not reach a screen as `130000.0000`.
    """

    @field_serializer("*", when_used="json")
    def _money_to_str(self, value: object) -> object:
        return str(for_display(value)) if isinstance(value, Decimal) else value
