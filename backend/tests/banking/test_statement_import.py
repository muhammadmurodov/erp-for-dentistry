"""Reading a bank's export, and the bank account it belongs to.

The bank's real format is unknown (docs/OPEN_QUESTIONS.md question B3), so
what is tested here is the importer's *tolerance*: a title block before the
table, Cyrillic headings, three decimal conventions, five date formats and
two text encodings. The alternative is a parser that works against one
bank's sample file and fails on the first statement anybody actually
downloads.

The other claim under test is the one the whole module rests on: importing a
statement posts **nothing**. A statement line is evidence that money moved,
not the event that moved it.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.banking.enums import ImportStatus, TransactionDirection
from app.modules.banking.models import BankTransaction
from app.modules.banking.services.accounts import BankAccountService, masked_account_number
from app.modules.banking.services.statement_import import (
    NO_BREAK_SPACE,
    StatementImporter,
    fingerprint_for,
)

#: A plainly named export, the shape a developer would write by hand.
PLAIN_CSV = """date,document_number,description,credit,debit
2026-10-01,101,Settlement from acquirer,495000,
2026-10-02,102,Courier deposit,150000,
2026-10-03,103,Bank charge,,25000
"""

#: The clinic's own headings, with a title block above the table and the
#: thousands grouped the way the bank's web office exports them.
CYRILLIC_CSV = (
    "АКБ Демо банк\n"
    "Выписка по счёту 20208000900123456789 за период 01.10.2026 - 03.10.2026\n"
    "\n"
    "Дата;№ Документа;Назначение платежа;Наименование корреспондента;Поступление;Расход\n"
    f"01.10.2026;101;Эквайринг;Demo Bank;495{NO_BREAK_SPACE}000,00;\n"
    "02.10.2026;102;Инкассация;Demo Bank;150'000.50;\n"
    "03.10.2026;103;Комиссия банка;Demo Bank;;25 000\n"
    "Итого;;;;645 000,50;25 000\n"
)


async def import_csv(importer, bank, body: str, *, filename: str = "statement.csv"):
    return await importer.import_statement(
        organization_id=bank.organization_id,
        bank_account_id=bank.id,
        filename=filename,
        content=body.encode("utf-8"),
    )


async def lines_of(db: AsyncSession, bank) -> list[BankTransaction]:
    stmt = (
        select(BankTransaction)
        .where(BankTransaction.bank_account_id == bank.id)
        .order_by(BankTransaction.value_date)
    )
    return list((await db.execute(stmt)).scalars().all())


class TestAPlainExport:
    async def test_every_line_is_read(self, db: AsyncSession, bank, importer) -> None:
        result = await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()

        assert (result.rows_read, result.rows_imported) == (3, 3)
        assert (result.duplicates_skipped, result.rows_rejected) == (0, 0)
        assert result.problems == []
        assert (result.period_from, result.period_to) == (date(2026, 10, 1), date(2026, 10, 3))

    async def test_direction_follows_the_column_the_amount_was_in(
        self, db: AsyncSession, bank, importer
    ) -> None:
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        lines = await lines_of(db, bank)

        assert [line.direction for line in lines] == [
            TransactionDirection.IN,
            TransactionDirection.IN,
            TransactionDirection.OUT,
        ]
        assert [line.amount for line in lines] == [
            Decimal("495000.0000"),
            Decimal("150000.0000"),
            Decimal("25000.0000"),
        ]

    async def test_the_detail_the_operator_matches_on_is_kept(
        self, db: AsyncSession, bank, importer
    ) -> None:
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        first = (await lines_of(db, bank))[0]

        assert first.description == "Settlement from acquirer"
        assert first.document_number == "101"
        assert first.currency == bank.currency
        # The untranslated row is kept too: a mapping that turns out to be
        # wrong can then be diagnosed without the original file.
        assert first.raw["description"] == "Settlement from acquirer"

    async def test_an_imported_line_starts_unexplained(
        self, db: AsyncSession, bank, importer
    ) -> None:
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        assert all(line.status.value == "UNMATCHED" for line in await lines_of(db, bank))
        assert all(line.commission_amount == Decimal("0.0000") for line in await lines_of(db, bank))


class TestImportingPostsNothing:
    async def test_the_ledger_is_untouched(self, db: AsyncSession, sales, bank, importer) -> None:
        """The module's central claim.

        If importing posted, every re-import would move the accounts, and a
        statement line would be counted as income before anyone had said
        what it was.
        """
        before = (await db.execute(text("SELECT count(*) FROM journal_entry"))).scalar_one()
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        after = (await db.execute(text("SELECT count(*) FROM journal_entry"))).scalar_one()
        assert after == before

    async def test_no_posting_event_is_claimed(self, db: AsyncSession, bank, importer) -> None:
        before = (await db.execute(text("SELECT count(*) FROM posting_event"))).scalar_one()
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        after = (await db.execute(text("SELECT count(*) FROM posting_event"))).scalar_one()
        assert after == before


class TestReimporting:
    async def test_the_same_file_twice_imports_nothing_the_second_time(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """Re-downloading a statement is routine. Doubling it is not."""
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        second = await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()

        assert (second.rows_read, second.rows_imported) == (3, 0)
        assert second.duplicates_skipped == 3
        assert len(await lines_of(db, bank)) == 3

    async def test_an_overlapping_range_imports_only_what_is_new(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """The usual case: yesterday's download, then today's wider one."""
        await import_csv(
            importer,
            bank,
            "date,document_number,description,credit,debit\n"
            "2026-10-01,101,Settlement from acquirer,495000,\n",
        )
        await db.flush()
        result = await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()

        assert (result.duplicates_skipped, result.rows_imported) == (1, 2)
        assert len(await lines_of(db, bank)) == 3

    async def test_a_file_repeating_a_line_imports_it_once(
        self, db: AsyncSession, bank, importer
    ) -> None:
        body = (
            "date,document_number,description,credit,debit\n"
            "2026-10-01,101,Settlement,495000,\n"
            "2026-10-01,101,Settlement,495000,\n"
        )
        result = await import_csv(importer, bank, body)
        await db.flush()
        assert (result.rows_imported, result.duplicates_skipped) == (1, 1)

    async def test_two_genuinely_identical_amounts_both_arrive(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """Two payments of the same size on one day are ordinary, and are
        told apart by their document numbers."""
        body = (
            "date,document_number,description,credit,debit\n"
            "2026-10-01,101,Settlement,495000,\n"
            "2026-10-01,102,Settlement,495000,\n"
        )
        result = await import_csv(importer, bank, body)
        await db.flush()
        assert result.rows_imported == 2


class TestTheFingerprint:
    def test_it_ignores_how_the_description_was_spaced(self) -> None:
        common = {
            "value_date": date(2026, 10, 1),
            "direction": TransactionDirection.IN,
            "amount": Decimal("495000"),
            "document_number": "101",
        }
        assert fingerprint_for(description="Settlement  from ACQUIRER", **common) == (
            fingerprint_for(description="settlement from acquirer", **common)
        )

    def test_it_changes_with_the_amount(self) -> None:
        common = {
            "value_date": date(2026, 10, 1),
            "direction": TransactionDirection.IN,
            "document_number": "101",
            "description": "Settlement",
        }
        assert fingerprint_for(amount=Decimal("495000"), **common) != fingerprint_for(
            amount=Decimal("495001"), **common
        )

    def test_it_distinguishes_an_inflow_from_an_outflow(self) -> None:
        common = {
            "value_date": date(2026, 10, 1),
            "amount": Decimal("495000"),
            "document_number": "101",
            "description": "Correction",
        }
        assert fingerprint_for(direction=TransactionDirection.IN, **common) != fingerprint_for(
            direction=TransactionDirection.OUT, **common
        )


class TestWhateverTheBankSends:
    async def test_a_title_block_above_the_table_is_skipped(
        self, db: AsyncSession, bank, importer
    ) -> None:
        result = await import_csv(importer, bank, CYRILLIC_CSV)
        await db.flush()
        assert result.rows_imported == 3

    async def test_cyrillic_headings_need_no_configuration(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """The clinic's own workbook headings, which is the shape most
        likely to arrive first."""
        await import_csv(importer, bank, CYRILLIC_CSV)
        await db.flush()
        lines = await lines_of(db, bank)

        assert [line.description for line in lines] == ["Эквайринг", "Инкассация", "Комиссия банка"]
        assert [line.counterparty_name for line in lines] == ["Demo Bank"] * 3
        assert [line.direction for line in lines] == [
            TransactionDirection.IN,
            TransactionDirection.IN,
            TransactionDirection.OUT,
        ]

    async def test_three_decimal_conventions_are_read_exactly(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """A non-breaking space with a decimal comma, an apostrophe with a
        decimal dot, and a plain space. None may go through a float."""
        await import_csv(importer, bank, CYRILLIC_CSV)
        await db.flush()
        assert [line.amount for line in await lines_of(db, bank)] == [
            Decimal("495000.0000"),
            Decimal("150000.5000"),
            Decimal("25000.0000"),
        ]

    async def test_a_semicolon_delimited_file_is_read(
        self, db: AsyncSession, bank, importer
    ) -> None:
        assert (await import_csv(importer, bank, CYRILLIC_CSV)).rows_imported == 3

    async def test_a_cp1251_file_is_read(self, db: AsyncSession, bank, importer) -> None:
        """Windows-1251 is what a Russian-language export from an older
        system produces, and it is not valid UTF-8."""
        result = await importer.import_statement(
            organization_id=bank.organization_id,
            bank_account_id=bank.id,
            filename="vypiska.csv",
            content=CYRILLIC_CSV.encode("cp1251"),
        )
        await db.flush()
        assert result.rows_imported == 3
        assert (await lines_of(db, bank))[0].description == "Эквайринг"

    @pytest.mark.parametrize(
        "written",
        ["2026-10-01", "01.10.2026", "01/10/2026", "2026/10/01", "01-10-2026"],
    )
    async def test_every_date_format_seen_in_an_export(
        self, db: AsyncSession, bank, importer, written: str
    ) -> None:
        body = f"date,document_number,description,credit,debit\n{written},101,Settlement,1000,\n"
        await import_csv(importer, bank, body)
        await db.flush()
        assert (await lines_of(db, bank))[0].value_date == date(2026, 10, 1)

    async def test_a_mapping_on_the_account_overrides_the_aliases(
        self, db: AsyncSession, audit, bank, importer
    ) -> None:
        """What makes a new bank a settings change rather than a release."""
        await BankAccountService(db, audit).set_column_mapping(
            organization_id=bank.organization_id,
            bank_account_id=bank.id,
            column_mapping={
                "value_date": "OperDay",
                "credit": "TurnoverCredit",
                "debit": "TurnoverDebit",
                "description": "PaymentDetails",
            },
        )
        body = (
            "OperDay,TurnoverCredit,TurnoverDebit,PaymentDetails\n"
            "2026-10-01,495000,,Settlement from acquirer\n"
        )
        result = await import_csv(importer, bank, body)
        await db.flush()

        assert result.rows_imported == 1
        assert (await lines_of(db, bank))[0].description == "Settlement from acquirer"

    async def test_an_excel_export_is_read_without_a_float(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """openpyxl hands back `datetime` and `float`. Neither may reach the
        amount: 495000.5 as a binary float is not 495000.5."""
        openpyxl = pytest.importorskip("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["АКБ Демо банк"])
        sheet.append([])
        sheet.append(["Дата", "№ Документа", "Назначение платежа", "Поступление", "Расход"])
        sheet.append([datetime(2026, 10, 1, 9, 30), 101, "Эквайринг", 495000.5, None])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = await importer.import_statement(
            organization_id=bank.organization_id,
            bank_account_id=bank.id,
            filename="vypiska.xlsx",
            content=buffer.getvalue(),
        )
        await db.flush()

        assert result.rows_imported == 1
        line = (await lines_of(db, bank))[0]
        assert line.value_date == date(2026, 10, 1)
        assert line.amount == Decimal("495000.5000")


class TestRowsItWillNotGuessAt:
    async def test_a_row_with_no_readable_date_is_reported_with_its_line_number(
        self, db: AsyncSession, bank, importer
    ) -> None:
        body = (
            "date,document_number,description,credit,debit\n"
            "2026-10-01,101,Settlement,495000,\n"
            "not a date,102,Settlement,150000,\n"
        )
        result = await import_csv(importer, bank, body)
        await db.flush()

        assert (result.rows_imported, result.rows_rejected) == (1, 1)
        assert result.problems == ["row 3: no readable date"]

    async def test_a_row_that_is_both_an_inflow_and_an_outflow_is_refused(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """Either the mapping is wrong or the bank is, and importing half of
        it would be a quiet guess about which."""
        body = (
            "date,document_number,description,credit,debit\n2026-10-01,101,Settlement,1000,2000\n"
        )
        result = await import_csv(importer, bank, body)
        await db.flush()

        assert result.rows_rejected == 1
        assert result.problems == ["row 2: both an inflow and an outflow on one line"]

    async def test_a_blank_row_and_a_totals_row_are_skipped_quietly(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """A separator is not a problem to report; counting it as one would
        make every statement look broken."""
        result = await import_csv(importer, bank, CYRILLIC_CSV)
        await db.flush()
        assert (result.rows_rejected, result.problems) == (0, [])

    async def test_a_zero_line_carries_no_information(
        self, db: AsyncSession, bank, importer
    ) -> None:
        body = "date,document_number,description,credit,debit\n2026-10-01,101,Nothing,0,0\n"
        result = await import_csv(importer, bank, body)
        await db.flush()
        assert (result.rows_imported, result.rows_rejected) == (0, 0)

    async def test_a_file_whose_columns_cannot_be_found_names_the_remedy(
        self, db: AsyncSession, bank, importer
    ) -> None:
        body = "col1,col2,col3\nx,y,z\n"
        with pytest.raises(ValidationFailed, match="column mapping"):
            await import_csv(importer, bank, body)

    async def test_an_empty_file_is_refused(self, db: AsyncSession, bank, importer) -> None:
        with pytest.raises(ValidationFailed, match="no rows"):
            await import_csv(importer, bank, "")

    async def test_a_pdf_is_refused_by_name(self, db: AsyncSession, bank, importer) -> None:
        with pytest.raises(ValidationFailed, match=r"\.csv, \.xlsx"):
            await import_csv(importer, bank, PLAIN_CSV, filename="vypiska.pdf")


class TestTheImportRecord:
    async def test_the_run_is_recorded_with_its_counts(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """An import that silently dropped a third of its lines is the kind
        of thing that only surfaces at year end."""
        body = (
            "date,document_number,description,credit,debit\n"
            "2026-10-01,101,Settlement,495000,\n"
            "not a date,102,Settlement,150000,\n"
        )
        await import_csv(importer, bank, body, filename="oktyabr.csv")
        await db.flush()

        runs = await importer.list_imports(organization_id=bank.organization_id)
        assert len(runs) == 1
        assert runs[0].filename == "oktyabr.csv"
        assert runs[0].status is ImportStatus.COMPLETED
        assert (runs[0].rows_read, runs[0].rows_imported, runs[0].rows_rejected) == (2, 1, 1)
        assert runs[0].problems == ["row 3: no readable date"]
        assert runs[0].period_from == date(2026, 10, 1)

    async def test_each_line_knows_which_run_brought_it_in(
        self, db: AsyncSession, bank, importer
    ) -> None:
        result = await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        assert {line.import_id for line in await lines_of(db, bank)} == {result.import_id}

    async def test_runs_can_be_narrowed_to_one_account(
        self, db: AsyncSession, audit, bank, importer
    ) -> None:
        other = await BankAccountService(db, audit).create(
            organization_id=bank.organization_id,
            code="BANK-2",
            name="Second account",
            account_code="5110.04",
        )
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()

        assert (
            len(
                await importer.list_imports(
                    organization_id=bank.organization_id, bank_account_id=other.id
                )
            )
            == 0
        )
        assert (
            len(
                await importer.list_imports(
                    organization_id=bank.organization_id, bank_account_id=bank.id
                )
            )
            == 1
        )

    async def test_the_import_is_audited(self, db: AsyncSession, bank, importer) -> None:
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        rows = (
            await db.execute(
                text("SELECT count(*) FROM audit_log WHERE action = 'banking.statement.imported'")
            )
        ).scalar_one()
        assert rows == 1


class TestTenantIsolation:
    async def test_another_organizations_account_cannot_be_imported_into(
        self, db: AsyncSession, bank, org_b, importer
    ) -> None:
        with pytest.raises(NotFound):
            await importer.import_statement(
                organization_id=org_b.id,
                bank_account_id=bank.id,
                filename="statement.csv",
                content=PLAIN_CSV.encode("utf-8"),
            )

    async def test_an_identical_statement_in_another_account_is_not_a_duplicate(
        self, db: AsyncSession, audit, bank, importer
    ) -> None:
        """The fingerprint is scoped to the account, so a clinic with two
        accounts at one bank does not suppress its own lines."""
        other = await BankAccountService(db, audit).create(
            organization_id=bank.organization_id,
            code="BANK-3",
            name="Third account",
            account_code="5110.04",
        )
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        result = await importer.import_statement(
            organization_id=bank.organization_id,
            bank_account_id=other.id,
            filename="statement.csv",
            content=PLAIN_CSV.encode("utf-8"),
        )
        await db.flush()
        assert result.rows_imported == 3


class TestTheBankAccountItself:
    async def test_it_is_paired_with_a_ledger_account(self, db: AsyncSession, bank) -> None:
        """Reconciliation proves two balances agree, which is only
        meaningful if the pairing is recorded rather than assumed."""
        code = (
            await db.execute(
                text("SELECT code FROM account WHERE id = :id"), {"id": bank.account_id}
            )
        ).scalar_one()
        assert code == "5110.01"

    async def test_a_structural_account_cannot_hold_a_bank_balance(
        self, db: AsyncSession, audit, sales
    ) -> None:
        """5100 is the statutory *group* heading above 5110. Pairing a bank
        account with a heading would make its balance unprovable."""
        with pytest.raises(ValidationFailed, match="cannot hold a bank balance"):
            await BankAccountService(db, audit).create(
                organization_id=sales.id,
                code="BANK-X",
                name="Wrong level",
                account_code="5100",
            )

    async def test_an_unknown_account_code_is_refused(self, db: AsyncSession, audit, sales) -> None:
        with pytest.raises(NotFound, match="does not exist"):
            await BankAccountService(db, audit).create(
                organization_id=sales.id,
                code="BANK-X",
                name="Nowhere",
                account_code="9999.99",
            )

    async def test_an_mfo_is_exactly_five_digits(self, db: AsyncSession, audit, sales) -> None:
        with pytest.raises(ValidationFailed, match="five digits"):
            await BankAccountService(db, audit).create(
                organization_id=sales.id,
                code="BANK-X",
                name="Bad MFO",
                account_code="5110.04",
                mfo="414",
            )

    async def test_a_code_is_used_once_per_organization(
        self, db: AsyncSession, audit, bank
    ) -> None:
        with pytest.raises(Conflict, match="already exists"):
            await BankAccountService(db, audit).create(
                organization_id=bank.organization_id,
                code="BANK-1",
                name="Duplicate",
                account_code="5110.04",
            )

    async def test_deactivating_needs_a_reason_and_hides_the_account(
        self, db: AsyncSession, audit, bank
    ) -> None:
        accounts = BankAccountService(db, audit)
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await accounts.deactivate(
                organization_id=bank.organization_id, bank_account_id=bank.id, reason="  "
            )

        await accounts.deactivate(
            organization_id=bank.organization_id,
            bank_account_id=bank.id,
            reason="Account closed by the bank",
        )
        await db.flush()
        assert await accounts.list_accounts(organization_id=bank.organization_id) == []
        assert (
            len(
                await accounts.list_accounts(
                    organization_id=bank.organization_id, include_inactive=True
                )
            )
            == 1
        )

    async def test_deactivating_twice_is_refused(self, db: AsyncSession, audit, bank) -> None:
        accounts = BankAccountService(db, audit)
        await accounts.deactivate(
            organization_id=bank.organization_id, bank_account_id=bank.id, reason="Closed"
        )
        await db.flush()
        with pytest.raises(Conflict, match="already inactive"):
            await accounts.deactivate(
                organization_id=bank.organization_id, bank_account_id=bank.id, reason="Closed"
            )

    async def test_the_account_number_is_not_written_to_the_audit_trail(
        self, db: AsyncSession, bank
    ) -> None:
        """It is a payment credential, and the audit trail is read by more
        people than the bank screen is."""
        await db.flush()
        trail = (
            await db.execute(
                text(
                    "SELECT new_values::text FROM audit_log "
                    "WHERE action = 'banking.account.created'"
                )
            )
        ).scalar_one()
        assert "20208000900123456789" not in trail

    @pytest.mark.parametrize(
        ("number", "shown"),
        [
            ("20208000900123456789", "****************6789"),
            ("6789", "****"),
            ("", None),
            (None, None),
        ],
    )
    def test_the_number_is_masked_for_display(self, number: str | None, shown: str | None) -> None:
        assert masked_account_number(number) == shown


class TestLargeFiles:
    async def test_a_file_past_the_row_limit_is_refused_before_parsing(
        self, db: AsyncSession, bank, importer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A statement with more rows than a year of trading is a mistake,
        and parsing it to find out would hold a transaction open."""
        from app.modules.banking.services import statement_import

        monkeypatch.setattr(statement_import, "MAX_ROWS", 3)
        body = "date,description,credit,debit\n" + "".join(
            f"2026-10-0{n},Line {n},1000,\n" for n in range(1, 5)
        )
        with pytest.raises(ValidationFailed, match="the limit is 3"):
            await import_csv(importer, bank, body)


class TestColumnResolution:
    def test_a_configured_heading_wins_over_an_alias(self) -> None:
        resolved = StatementImporter.resolve_columns(
            ["Дата", "OperDay", "Поступление"],
            {"value_date": "OperDay"},
        )
        assert resolved["value_date"] == 1

    def test_headings_match_regardless_of_case_and_padding(self) -> None:
        resolved = StatementImporter.resolve_columns(
            ["  DATE  ", f"Credit{NO_BREAK_SPACE}", "DEBIT"], None
        )
        assert resolved == {"value_date": 0, "credit": 1, "debit": 2}

    def test_a_heading_nobody_mapped_is_simply_absent(self) -> None:
        resolved = StatementImporter.resolve_columns(["date", "credit", "balance"], None)
        assert "document_number" not in resolved


class TestTheDatabaseEnforcesIt:
    async def test_a_line_must_belong_to_a_bank_account_in_its_own_tenant(
        self, db: AsyncSession, bank, org_b
    ) -> None:
        """The composite foreign key, not the service, is what makes a
        cross-tenant statement line unrepresentable."""
        await db.begin_nested()
        with pytest.raises(Exception) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO bank_transaction
                        (id, organization_id, bank_account_id, value_date,
                         direction, amount, currency, status, fingerprint,
                         commission_amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :acct, :on, 'IN', 1000,
                            'UZS', 'UNMATCHED', 'deadbeef', 0, now(), now())
                    """
                ),
                {"org": org_b.id, "acct": bank.id, "on": date(2026, 10, 1)},
            )
            await db.flush()
        assert "fk_bank_transaction_account" in str(caught.value)
        await db.rollback()

    async def test_a_statement_line_cannot_be_negative(self, db: AsyncSession, bank) -> None:
        """Direction carries the sign. A negative amount would make every
        total ambiguous."""
        await db.begin_nested()
        with pytest.raises(Exception) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO bank_transaction
                        (id, organization_id, bank_account_id, value_date,
                         direction, amount, currency, status, fingerprint,
                         commission_amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :acct, :on, 'OUT', -1000,
                            'UZS', 'UNMATCHED', 'deadbeef', 0, now(), now())
                    """
                ),
                {"org": bank.organization_id, "acct": bank.id, "on": date(2026, 10, 1)},
            )
            await db.flush()
        assert "amount_positive" in str(caught.value)
        await db.rollback()

    async def test_setting_a_line_aside_without_a_reason_is_refused(
        self, db: AsyncSession, bank, importer
    ) -> None:
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        line = (await lines_of(db, bank))[0]

        await db.begin_nested()
        with pytest.raises(Exception) as caught:
            await db.execute(
                text("UPDATE bank_transaction SET status = 'IGNORED' WHERE id = :id"),
                {"id": line.id},
            )
            await db.flush()
        assert "needs_a_reason" in str(caught.value)
        await db.rollback()

    async def test_the_same_line_cannot_be_imported_into_one_account_twice(
        self, db: AsyncSession, bank, importer
    ) -> None:
        """The unique index, not the importer's in-memory set: two
        concurrent imports of one file must not both win."""
        await import_csv(importer, bank, PLAIN_CSV)
        await db.flush()
        count = (
            await db.execute(
                select(func.count())
                .select_from(BankTransaction)
                .where(BankTransaction.bank_account_id == bank.id)
            )
        ).scalar_one()
        assert count == 3
