"""Monetary arithmetic.

Rules this module exists to enforce:

* Money is `decimal.Decimal`. Binary floats never touch an amount. Passing a
  `float` to `to_amount` raises, rather than silently importing a rounding
  error into the ledger.
* Every amount is quantized to the scale of its currency before it is stored
  or compared, using `ROUND_HALF_UP` (the convention the clinic's accountant
  already applies by hand, and the one Uzbek statutory forms assume).
* `allocate` splits an amount without losing or inventing minor units, which
  is what makes a payment allocation across invoices add back to the payment.

The database column type is `NUMERIC(20, 4)`; see `MONEY_SCALE`. Four stored
decimals leave headroom for intermediate values such as a VAT extraction or a
commission percentage, while the *posted* value of a UZS amount is always
quantized to that currency's two places first.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from sqlalchemy import Numeric

from app.core.errors import ValidationFailed

#: Precision/scale of every monetary database column.
MONEY_PRECISION: Final = 20
MONEY_SCALE: Final = 4

#: SQLAlchemy type for monetary columns. `asdecimal=True` keeps values as
#: `Decimal` on the way back out of the driver.
MoneyType: Final = Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True)

ZERO: Final = Decimal("0")

#: Fallback scale per currency used before the `currency` row is loaded.
#: UZS has two statutory minor places (tiyin), even though coins are no longer
#: in circulation and the clinic's receipts are whole sums in practice.
DEFAULT_CURRENCY_SCALES: Final[dict[str, int]] = {"UZS": 2, "USD": 2, "EUR": 2, "RUB": 2}


def currency_scale(currency_code: str) -> int:
    try:
        return DEFAULT_CURRENCY_SCALES[currency_code.upper()]
    except KeyError as exc:
        raise ValidationFailed(
            f"Unknown currency code {currency_code!r}",
            details={"currency": currency_code},
        ) from exc


def to_amount(value: Decimal | int | str) -> Decimal:
    """Coerce an untrusted input into a monetary `Decimal`.

    `float` is rejected on purpose: ``0.1 + 0.2`` is not ``0.3`` and an ERP has
    no business discovering that in a trial balance.
    """
    if isinstance(value, float):
        raise ValidationFailed(
            "Monetary values must not be binary floats; pass Decimal, int or str",
            details={"received_type": "float"},
        )
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationFailed(f"{value!r} is not a valid monetary amount") from exc
    if not amount.is_finite():
        raise ValidationFailed("Monetary amounts must be finite")
    return amount


def quantize(value: Decimal | int | str, currency_code: str = "UZS") -> Decimal:
    """Round `value` to the scale of `currency_code`, half away from zero."""
    amount = to_amount(value)
    exponent = Decimal(1).scaleb(-currency_scale(currency_code))
    return amount.quantize(exponent, rounding=ROUND_HALF_UP)


def is_zero(value: Decimal) -> bool:
    return to_amount(value) == ZERO


def allocate(total: Decimal, weights: list[Decimal], currency_code: str = "UZS") -> list[Decimal]:
    """Split `total` in proportion to `weights` with no cent lost or created.

    The remainder left by rounding is distributed one minor unit at a time to
    the largest weights first, so ``sum(allocate(t, w)) == quantize(t)`` always
    holds. Used when a single settlement or payment has to be spread over
    several invoices.
    """
    total = quantize(total, currency_code)
    if not weights:
        raise ValidationFailed("allocate() requires at least one weight")
    cleaned = [to_amount(w) for w in weights]
    if any(w < ZERO for w in cleaned):
        raise ValidationFailed("Allocation weights must not be negative")
    weight_sum = sum(cleaned, ZERO)
    if weight_sum == ZERO:
        raise ValidationFailed("Allocation weights must not sum to zero")

    scale = currency_scale(currency_code)
    minor_unit = Decimal(1).scaleb(-scale)
    shares = [quantize(total * w / weight_sum, currency_code) for w in cleaned]
    drift = total - sum(shares, ZERO)

    # Hand the rounding drift out in minor units, biggest weight first.
    steps = int((abs(drift) / minor_unit).to_integral_value())
    direction = minor_unit if drift > ZERO else -minor_unit
    order = sorted(range(len(cleaned)), key=lambda i: cleaned[i], reverse=True)
    for n in range(steps):
        shares[order[n % len(order)]] += direction
    return shares


def sum_amounts(values: list[Decimal], currency_code: str = "UZS") -> Decimal:
    return quantize(sum((to_amount(v) for v in values), ZERO), currency_code)


def for_display(value: Decimal) -> Decimal:
    """Strip the storage scale's padding without changing the value.

    Monetary columns are `NUMERIC(20, 4)` so that intermediate values have
    headroom, but every amount is quantized to its currency's scale before it
    is written. The extra zeros are therefore an artifact of the column, and
    `130000.0000` reaching a screen as `130000.0000` is a presentation bug —
    one that appeared three times before this helper existed.

    `normalize()` only removes trailing zeros, so this is lossless. A minimum
    of two decimal places is then restored, because money reads as money. A
    currency with more places keeps them: a genuine `1.2345` normalizes to
    `1.2345` and is left alone.
    """
    normalized = to_amount(value).normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        return normalized.quantize(Decimal("0.01"))
    return normalized
