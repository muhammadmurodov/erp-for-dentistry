"""Readers for the files in `app/reference/data`.

These return validated, typed records and verify the file's own provenance
notice is intact, so a silently-edited reference file is caught rather than
seeded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import ValidationFailed

DATA_DIR = Path(__file__).parent / "data"

CHART_OF_ACCOUNTS_FILE = "uz_chart_of_accounts.json"
TIMESHEET_CODES_FILE = "uz_timesheet_codes.json"

#: Levels in the Uzbek chart, outermost first.
ACCOUNT_LEVELS = ("PART", "SECTION", "GROUP", "ACCOUNT", "SUBACCOUNT", "SYNTHETIC")

#: Debit/credit nature as the statutory chart records it.
ACCOUNT_NATURES = (
    "ASSET",
    "LIABILITY_EQUITY",
    "CONTRA_ASSET",
    "CONTRA_LIABILITY",
    "TRANSIT",
    "OFF_BALANCE",
)

ACCOUNT_ELEMENTS = ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE", "RESULT")


@dataclass(frozen=True, slots=True)
class ReferenceAccount:
    code: str
    name_uz_cyrillic: str
    level: str
    nature: str | None
    parent_code: str | None
    commonly_used: bool
    statutory_part: str | None
    element: str | None
    range_label: str | None
    #: True for every row: the derived classification is unconfirmed until an
    #: accountant signs it off. Carried through so a seeding command can mark
    #: the resulting accounts as provisional rather than authoritative.
    needs_confirmation: bool

    @property
    def is_postable(self) -> bool:
        """Whether a journal line may reference this row.

        Parts, sections and groups are structural: they aggregate balances and
        must never carry a posting of their own, which is what keeps a trial
        balance from double-counting.
        """
        return self.level in ("ACCOUNT", "SUBACCOUNT", "SYNTHETIC")


@dataclass(frozen=True, slots=True)
class TimesheetCode:
    code: str
    name_uz: str
    name_ru: str
    mark_uz: str
    mark_ru: str

    @property
    def has_uzbek(self) -> bool:
        """False for the codes the source workbook never translated.

        12 of the 25 codes carry Russian only. The Uzbek wording is statutory
        and is not guessed anywhere in this project, so the payroll module
        must either obtain it or display the Russian text and say so.
        """
        return bool(self.name_uz and self.mark_uz)


def _load(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        raise ValidationFailed(
            f"Reference file {filename!r} is missing",
            details={"expected_path": str(path)},
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    # The notice records that the classification is unverified. Losing it
    # would turn provisional data into apparently authoritative data.
    if not payload.get("notice"):
        raise ValidationFailed(
            f"Reference file {filename!r} has lost its provenance notice",
            details={"file": filename},
        )
    return payload


@lru_cache
def load_chart_of_accounts() -> tuple[ReferenceAccount, ...]:
    payload = _load(CHART_OF_ACCOUNTS_FILE)
    accounts: list[ReferenceAccount] = []
    seen: set[str] = set()
    for row in payload["accounts"]:
        code = str(row["code"]).strip()
        if code in seen:
            raise ValidationFailed(
                f"Duplicate account code {code!r} in the reference chart",
                details={"code": code},
            )
        seen.add(code)
        level = row["level"]
        if level not in ACCOUNT_LEVELS:
            raise ValidationFailed(
                f"Account {code!r} has unknown level {level!r}",
                details={"code": code, "level": level},
            )
        nature = row.get("nature")
        if nature is not None and nature not in ACCOUNT_NATURES:
            raise ValidationFailed(
                f"Account {code!r} has unknown nature {nature!r}",
                details={"code": code, "nature": nature},
            )
        element = row.get("element")
        if element is not None and element not in ACCOUNT_ELEMENTS:
            raise ValidationFailed(
                f"Account {code!r} has unknown element {element!r}",
                details={"code": code, "element": element},
            )
        accounts.append(
            ReferenceAccount(
                code=code,
                name_uz_cyrillic=row["name_uz_cyrillic"],
                level=level,
                nature=nature,
                parent_code=row.get("parent_code"),
                commonly_used=bool(row.get("commonly_used")),
                statutory_part=row.get("statutory_part"),
                element=element,
                range_label=row.get("range_label"),
                needs_confirmation=bool(row.get("needs_confirmation", True)),
            )
        )
    if not accounts:
        raise ValidationFailed("The reference chart of accounts is empty")
    return tuple(accounts)


@lru_cache
def load_timesheet_codes() -> tuple[TimesheetCode, ...]:
    payload = _load(TIMESHEET_CODES_FILE)
    codes = tuple(
        TimesheetCode(
            code=str(row["code"]).strip(),
            name_uz=row["name_uz"],
            name_ru=row["name_ru"],
            mark_uz=row["mark_uz"],
            mark_ru=row["mark_ru"],
        )
        for row in payload["codes"]
    )
    # The file declares which codes are known to lack Uzbek text. If the data
    # and the declaration disagree, one of them was edited without the other
    # and the gap is no longer being tracked honestly.
    declared = set(payload["known_translation_gaps"]["codes_missing_uzbek"])
    actual = {c.code for c in codes if not c.has_uzbek}
    if declared != actual:
        raise ValidationFailed(
            "The declared translation gaps no longer match the data",
            details={
                "declared_but_complete": sorted(declared - actual),
                "undeclared_gaps": sorted(actual - declared),
            },
        )
    return codes


def timesheet_codes_missing_uzbek() -> tuple[str, ...]:
    """Codes that have no Uzbek statutory wording yet."""
    return tuple(c.code for c in load_timesheet_codes() if not c.has_uzbek)


def postable_accounts() -> tuple[ReferenceAccount, ...]:
    """Only the rows a journal line may reference."""
    return tuple(a for a in load_chart_of_accounts() if a.is_postable)
