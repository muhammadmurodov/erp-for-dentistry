"""Monetary arithmetic must never lose, invent or round a value by accident."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.core.money import (
    allocate,
    currency_scale,
    quantize,
    sum_amounts,
    to_amount,
)


class TestFloatsAreRejected:
    def test_float_input_raises(self) -> None:
        with pytest.raises(ValidationFailed, match="must not be binary floats"):
            to_amount(300000.0)

    def test_decimal_from_string_is_exact(self) -> None:
        # The classic float failure, which must not be reachable here.
        assert to_amount("0.1") + to_amount("0.2") == Decimal("0.3")

    def test_integer_input_is_accepted(self) -> None:
        assert to_amount(300_000) == Decimal("300000")

    @pytest.mark.parametrize("bad", ["", "abc", "1,5", "NaN", "Infinity"])
    def test_nonsense_input_raises(self, bad: str) -> None:
        with pytest.raises(ValidationFailed):
            to_amount(bad)


class TestQuantize:
    def test_uzs_has_two_places(self) -> None:
        assert currency_scale("UZS") == 2

    def test_rounds_half_away_from_zero(self) -> None:
        assert quantize("0.005") == Decimal("0.01")
        assert quantize("-0.005") == Decimal("-0.01")

    def test_unknown_currency_raises(self) -> None:
        with pytest.raises(ValidationFailed, match="Unknown currency"):
            quantize("1.00", "XYZ")

    def test_whole_som_amount_is_unchanged(self) -> None:
        assert quantize("300000") == Decimal("300000.00")


class TestAllocate:
    def test_even_split_is_exact(self) -> None:
        shares = allocate(Decimal("300000"), [Decimal(1)] * 3)
        assert shares == [Decimal("100000.00")] * 3

    def test_indivisible_amount_loses_nothing(self) -> None:
        """The property that matters: the parts add back to the whole."""
        total = Decimal("100.01")
        shares = allocate(total, [Decimal(1), Decimal(2)])
        assert sum_amounts(shares) == quantize(total)

    @pytest.mark.parametrize(
        "total,weights",
        [
            ("0.03", [1, 1, 1]),
            ("0.01", [1, 1, 1]),
            ("1000000.01", [7, 11, 13]),
            ("495000", [1, 1, 1, 1, 1, 1, 1]),
            ("1", [1, 1, 1]),
        ],
    )
    def test_sum_of_shares_always_equals_total(self, total, weights) -> None:
        shares = allocate(Decimal(total), [Decimal(w) for w in weights])
        assert sum_amounts(shares) == quantize(Decimal(total))

    def test_zero_weight_receives_nothing(self) -> None:
        shares = allocate(Decimal("100"), [Decimal(1), Decimal(0)])
        assert shares[1] == Decimal("0.00")
        assert sum_amounts(shares) == Decimal("100.00")

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValidationFailed, match="must not be negative"):
            allocate(Decimal("100"), [Decimal(1), Decimal(-1)])

    def test_all_zero_weights_raises(self) -> None:
        with pytest.raises(ValidationFailed, match="must not sum to zero"):
            allocate(Decimal("100"), [Decimal(0), Decimal(0)])

    def test_empty_weights_raises(self) -> None:
        with pytest.raises(ValidationFailed, match="at least one weight"):
            allocate(Decimal("100"), [])


class TestWorkbookDerivedScenarios:
    """Shapes taken from the legacy workbook, with fictional amounts.

    The reference workbook grosses a card settlement up with
    ``net / (1 - commission)``. The new system records the gross payment and
    the fee separately instead; these tests pin the arithmetic either way
    agrees on, so Phase 3 cannot drift from it silently.
    """

    def test_card_payment_splits_into_settlement_and_fee(self) -> None:
        gross = quantize("500000")
        fee = quantize("5000")
        settlement = quantize(gross - fee)
        assert settlement == Decimal("495000.00")
        # The economic identity the ledger must preserve.
        assert settlement + fee == gross

    def test_gross_up_from_net_matches_stated_fee(self) -> None:
        net = quantize("495000")
        commission_rate = Decimal("0.01")
        gross = quantize(net / (Decimal(1) - commission_rate))
        assert gross == Decimal("500000.00")

    def test_vat_extraction_from_a_gross_price(self) -> None:
        """VAT is extracted, not added, when a price is VAT-inclusive.

        Whether the clinic's dental services are VAT-bearing at all is an
        open question for the accountant (see docs/OPEN_QUESTIONS.md). This
        test fixes only the arithmetic, at an illustrative 12% rate.
        """
        gross = quantize("112000")
        rate = Decimal("0.12")
        net = quantize(gross / (Decimal(1) + rate))
        vat = quantize(gross - net)
        assert net == Decimal("100000.00")
        assert vat == Decimal("12000.00")
        assert net + vat == gross


class TestDisplayFormatting:
    """`for_display` strips the storage scale's padding, losslessly.

    Columns are NUMERIC(20, 4) for intermediate headroom, but every stored
    amount is already quantized to its currency. The padding is an artifact
    of the column, and it leaked to the screen three times before this
    existed.
    """

    @pytest.mark.parametrize(
        "stored,shown",
        [
            ("130000.0000", "130000.00"),
            ("300000.00", "300000.00"),
            ("-5000.0000", "-5000.00"),
            ("0.0000", "0.00"),
            ("17020.6500", "17020.65"),
            ("0.01", "0.01"),
        ],
    )
    def test_padding_is_stripped(self, stored: str, shown: str) -> None:
        from app.core.money import for_display

        assert str(for_display(Decimal(stored))) == shown

    def test_a_currency_with_more_places_keeps_them(self) -> None:
        """Only trailing zeros are removed; significant digits never are."""
        from app.core.money import for_display

        assert str(for_display(Decimal("1.2345"))) == "1.2345"

    def test_the_value_is_never_changed(self) -> None:
        from app.core.money import for_display

        for raw in ("130000.0000", "1.2345", "0.01", "-5000.5000"):
            assert for_display(Decimal(raw)) == Decimal(raw)
