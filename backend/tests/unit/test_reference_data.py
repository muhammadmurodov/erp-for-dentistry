"""The statutory reference data must stay structurally sound.

It will seed every organization's chart of accounts, so a defect here would
propagate into real books.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.reference.loader import (
    load_chart_of_accounts,
    load_timesheet_codes,
    postable_accounts,
    timesheet_codes_missing_uzbek,
)


class TestChartOfAccounts:
    def test_it_loads(self) -> None:
        accounts = load_chart_of_accounts()
        assert len(accounts) > 300

    def test_codes_are_unique(self) -> None:
        codes = [a.code for a in load_chart_of_accounts()]
        assert len(codes) == len(set(codes))

    def test_every_parent_reference_resolves(self) -> None:
        """A dangling parent would break the hierarchy a balance sheet rolls up."""
        accounts = load_chart_of_accounts()
        codes = {a.code for a in accounts}
        dangling = [
            (a.code, a.parent_code)
            for a in accounts
            if a.parent_code is not None and a.parent_code not in codes
        ]
        assert not dangling, f"unresolved parents: {dangling}"

    def test_no_cycles_in_the_hierarchy(self) -> None:
        accounts = {a.code: a for a in load_chart_of_accounts()}
        for start in accounts:
            seen: set[str] = set()
            current: str | None = start
            while current is not None:
                if current in seen:
                    pytest.fail(f"cycle reached from {start}: {seen}")
                seen.add(current)
                current = accounts[current].parent_code if current in accounts else None

    def test_structural_rows_are_not_postable(self) -> None:
        """Posting to a group would double-count it against its children."""
        for account in load_chart_of_accounts():
            if account.level in ("PART", "SECTION", "GROUP"):
                assert not account.is_postable, account.code

    def test_postable_accounts_are_leaf_level(self) -> None:
        for account in postable_accounts():
            assert account.level in ("ACCOUNT", "SUBACCOUNT", "SYNTHETIC")

    def test_classification_is_flagged_unconfirmed(self) -> None:
        """Derived classification must not present itself as authoritative."""
        assert all(a.needs_confirmation for a in load_chart_of_accounts())

    @pytest.mark.parametrize(
        "code,element",
        [
            ("5010", "ASSET"),  # cash in hand
            ("5110", "ASSET"),  # bank current account
            ("5710", "ASSET"),  # money in transit
            ("6010", "LIABILITY"),  # payable to suppliers
            ("6710", "LIABILITY"),  # wages payable
            ("7810", "LIABILITY"),  # long-term bank loan
            ("8710", "EQUITY"),  # retained earnings
            ("9030", "REVENUE"),  # revenue from services rendered
            ("9130", "EXPENSE"),  # cost of services rendered
            ("9420", "EXPENSE"),  # administrative expenses
            ("9610", "EXPENSE"),  # financial expenses
            ("9910", "RESULT"),  # profit and loss summary
        ],
    )
    def test_accounts_the_legacy_workbook_posts_to_are_classified(
        self, code: str, element: str
    ) -> None:
        """Cross-check against how the workbook actually used each account.

        These twelve accounts appear in the legacy accounting-policy sheet's
        debit/credit mappings, so their classification is observable rather
        than assumed. If a future edit to the reference file reclassifies one,
        the P&L and balance sheet would silently change shape.
        """
        account = next((a for a in load_chart_of_accounts() if a.code == code), None)
        assert account is not None, f"account {code} is missing"
        assert account.element == element
        assert account.is_postable

    def test_sub_accounts_inherit_their_base_classification(self) -> None:
        """`5110.01` is a sub-account of `5110` and classifies the same way."""
        accounts = {a.code: a for a in load_chart_of_accounts()}
        base = accounts.get("5110")
        sub = accounts.get("5110.01")
        assert base is not None and sub is not None
        assert sub.element == base.element == "ASSET"
        assert sub.statutory_part == base.statutory_part

    def test_only_structural_headers_are_unclassified(self) -> None:
        unclassified = [a for a in load_chart_of_accounts() if a.element is None]
        assert all(a.level in ("PART", "SECTION") for a in unclassified), [
            (a.code, a.level) for a in unclassified
        ]


class TestTimesheetCodes:
    def test_it_loads(self) -> None:
        codes = load_timesheet_codes()
        assert len(codes) >= 20

    def test_codes_are_unique(self) -> None:
        codes = [c.code for c in load_timesheet_codes()]
        assert len(codes) == len(set(codes))

    def test_every_code_has_russian_text(self) -> None:
        for entry in load_timesheet_codes():
            assert entry.name_ru and entry.mark_ru, entry.code

    def test_the_uzbek_translation_gap_is_exactly_what_is_declared(self) -> None:
        """12 codes are Russian-only in the source. The gap is recorded, not guessed.

        This test fails if a new gap appears (so it cannot creep in unnoticed)
        and also if a gap is filled without updating the declaration (so the
        file stops understating its own completeness).
        """
        missing = set(timesheet_codes_missing_uzbek())
        assert missing == {
            "22",
            "24",
            "25",
            "26",
            "27",
            "28",
            "29",
            "31",
            "32",
            "33",
            "34",
            "35",
        }

    def test_a_mismatched_gap_declaration_is_refused(self, tmp_path, monkeypatch) -> None:
        import json

        from app.reference import loader

        doctored = tmp_path / loader.TIMESHEET_CODES_FILE
        doctored.write_text(
            json.dumps(
                {
                    "notice": "test fixture",
                    "codes": [
                        {
                            "code": "01",
                            "name_uz": "",
                            "name_ru": "Часы работы",
                            "mark_uz": "",
                            "mark_ru": "Я",
                        }
                    ],
                    # Claims completeness the data does not have.
                    "known_translation_gaps": {"codes_missing_uzbek": []},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        loader.load_timesheet_codes.cache_clear()
        with pytest.raises(ValidationFailed, match="declared translation gaps"):
            loader.load_timesheet_codes()
        loader.load_timesheet_codes.cache_clear()


class TestProvenanceGuard:
    def test_a_file_without_its_notice_is_refused(self, tmp_path, monkeypatch) -> None:
        """Dropping the notice would turn provisional data into apparent fact."""
        import json

        from app.reference import loader

        stripped = tmp_path / loader.CHART_OF_ACCOUNTS_FILE
        stripped.write_text(json.dumps({"accounts": []}), encoding="utf-8")
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        loader.load_chart_of_accounts.cache_clear()
        with pytest.raises(ValidationFailed, match="provenance notice"):
            loader.load_chart_of_accounts()
        loader.load_chart_of_accounts.cache_clear()
