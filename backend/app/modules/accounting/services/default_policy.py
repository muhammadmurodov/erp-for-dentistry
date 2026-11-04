"""A starting accounting policy, derived from the legacy workbook.

The clinic's existing workbook maps 77 operation types to debit/credit
account pairs on its `Учетная_политика` sheet. The rules below are the subset
those mappings support for the operations this system posts today.

Three things make this safe to ship where transcribing the sheet would not
be:

* It creates a **draft**. Nothing posts under it until somebody with
  `accounting.policy.manage` activates it, which is audited.
* Each rule carries `source_note` recording where it came from, so a reviewer
  can check it against the workbook rather than taking it on trust.
* Rules the workbook's own author flagged as doubtful are **not** included —
  see `docs/OPEN_QUESTIONS.md` question A4.

It is a starting point for an accountant to review, not an authority.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.models import AccountingPolicy
from app.modules.accounting.services.accounts import AccountService
from app.modules.accounting.services.policy import PolicyService
from app.modules.audit.service import AuditRecorder


@dataclass(frozen=True, slots=True)
class DefaultRule:
    operation_code: str
    description: str
    description_uz: str
    description_ru: str
    debit: str
    credit: str
    source_note: str


#: Account codes referenced below, for the preflight check.
#: 1010 materials · 4010 receivables · 4310 advances paid to suppliers
#: 5010 cash in hand · 5110.01 bank current account
#: 5110.03 card settlement transit · 6010 supplier payable
#: 6310 advances received · 9030 revenue · 9420 administrative expense
#: 6411 income tax payable · 6520.01 social tax payable · 6520.02 INPS
#: 6520.03 employee pension payable · 6710 wages payable to staff
DEFAULT_RULES: tuple[DefaultRule, ...] = (
    DefaultRule(
        operation_code="invoice_issued",
        description="Invoice issued to a customer — revenue recognised",
        description_uz="Mijozga hisob-faktura berildi — daromad tan olindi",
        description_ru="Выставлен счёт покупателю — признана выручка",
        debit="4010",
        credit="9030",
        source_note=(
            "Workbook row 'Асосий фаолиятга кўрсатилган хизматлар учун счёт' "
            "credits 9030 for services rendered; the debit is the receivable "
            "rather than cash because issuing precedes collection."
        ),
    ),
    DefaultRule(
        operation_code="payment_received_cash",
        description="Cash received from a customer, settling a receivable",
        description_uz="Mijozdan naqd pul olindi, qarz yopildi",
        description_ru="Получены наличные от покупателя, погашена задолженность",
        debit="5010",
        credit="4010",
        source_note=(
            "Workbook 'Нақд пулга хизмат кўрсатиш' debits 5010. It credits "
            "9030 directly because that sheet recognises revenue on receipt; "
            "here the receivable was already raised at issue, so the credit "
            "settles it instead. Revenue is still recognised exactly once."
        ),
    ),
    DefaultRule(
        operation_code="payment_received_card",
        description="Card payment taken, settling a receivable",
        description_uz="Plastik karta orqali to'lov, qarz yopildi",
        description_ru="Оплата картой, погашена задолженность",
        debit="5110.03",
        credit="4010",
        source_note=(
            "Workbook 'Пластик картага хизмат кўрсатиш' debits the card "
            "transit account 5110.03. The money is owed by the acquirer until "
            "it settles, which is what the transit account represents."
        ),
    ),
    DefaultRule(
        operation_code="payment_received_bank",
        description="Bank transfer received, settling a receivable",
        description_uz="Bank o'tkazmasi olindi, qarz yopildi",
        description_ru="Получен банковский перевод, погашена задолженность",
        debit="5110.01",
        credit="4010",
        source_note="Workbook uses 5110.01 as the main current account throughout.",
    ),
    DefaultRule(
        operation_code="advance_received_cash",
        description="Cash advance received before any invoice — a liability",
        description_uz="Hisob-fakturasiz oldindan naqd to'lov — majburiyat",
        description_ru="Аванс наличными до выставления счёта — обязательство",
        debit="5010",
        credit="6310",
        source_note=(
            "Not in the workbook: it has no advance-from-customer rule, only "
            "advances *paid* to suppliers (4310/4330). 6310 is the statutory "
            "counterpart for advances received. Needs confirmation."
        ),
    ),
    DefaultRule(
        operation_code="advance_received_card",
        description="Card advance received before any invoice — a liability",
        description_uz="Hisob-fakturasiz oldindan karta to'lovi — majburiyat",
        description_ru="Аванс картой до выставления счёта — обязательство",
        debit="5110.03",
        credit="6310",
        source_note="As advance_received_cash, via the card transit account.",
    ),
    DefaultRule(
        operation_code="advance_received_bank",
        description="Bank advance received before any invoice — a liability",
        description_uz="Hisob-fakturasiz oldindan bank to'lovi — majburiyat",
        description_ru="Аванс переводом до выставления счёта — обязательство",
        debit="5110.01",
        credit="6310",
        source_note="As advance_received_cash, via the main current account.",
    ),
    DefaultRule(
        operation_code="advance_applied_to_invoice",
        description="An existing advance applied to an invoice",
        description_uz="Oldingi avans hisob-fakturaga o'tkazildi",
        description_ru="Ранее полученный аванс зачтён в счёт",
        debit="6310",
        credit="4010",
        source_note=(
            "Mirrors the workbook's supplier-advance clearing pattern "
            "('Олдиндан тўланган аванслар учун счёт фактуралар', 6010/4310) "
            "on the customer side."
        ),
    ),
    DefaultRule(
        operation_code="card_settlement_received",
        description="Acquirer settles card takings, net of commission",
        description_uz="Bank karta tushumini komissiyani ushlab o'tkazdi",
        description_ru="Эквайер перечислил выручку по картам за вычетом комиссии",
        debit="5110.01",
        credit="5110.03",
        source_note=(
            "Workbook 'Асосий ҳисобрақамга транзитдан пул ўтказиш (онлайн)'. "
            "The commission line is added by the settlement service, which "
            "knows the actual fee; the workbook instead grossed up from the "
            "net receipt and never recorded the fee as a cost."
        ),
    ),
    DefaultRule(
        operation_code="bank_commission",
        description="Bank or acquirer commission charged",
        description_uz="Bank komissiyasi hisoblandi",
        description_ru="Начислена комиссия банка",
        debit="9420",
        credit="5110.03",
        source_note=(
            "The workbook classifies bank charges under 'Харажат. Банк "
            "хизматлари'; 9420 is its administrative-expense account."
        ),
    ),
    DefaultRule(
        operation_code="cash_deposit_via_staff",
        description="Cash sent to the bank by an employee",
        description_uz="Naqd pul xodim orqali bankka topshirildi",
        description_ru="Наличные сданы в банк через сотрудника",
        debit="5710.01",
        credit="5010",
        source_note=(
            "Workbook 'Нақд пулни банкга ходимлар орқали топшириш', with the "
            "documents it names: расходный кассовый ордер and the bank's "
            "receipt. Posts to transit, not to the bank account, because the "
            "money has left the till but has not yet arrived."
        ),
    ),
    DefaultRule(
        operation_code="cash_deposit_via_collection",
        description="Cash collected by the bank's cash-in-transit service",
        description_uz="Naqd pul inkassatsiya orqali bankka topshirildi",
        description_ru="Наличные сданы в банк через инкассацию",
        debit="5710.02",
        credit="5010",
        source_note=(
            "Workbook 'Инкассация орқали банкга пул топшириш'. A separate "
            "transit account from the staff route, because the money is in "
            "different hands."
        ),
    ),
    DefaultRule(
        operation_code="bank_deposit_confirmed_staff",
        description="Bank confirmed cash carried in by staff",
        description_uz="Xodim topshirgan naqd pul bank tomonidan tasdiqlandi",
        description_ru="Банк подтвердил наличные, сданные сотрудником",
        debit="5110.01",
        credit="5710.01",
        source_note=(
            "The closing half of the workbook's staff deposit route: the "
            "transit account 5710.01 is cleared only when the statement "
            "shows the money arrived. Until then the cash is genuinely in "
            "transit, which is what the transit account is for."
        ),
    ),
    DefaultRule(
        operation_code="bank_deposit_confirmed_collection",
        description="Bank confirmed cash taken by the collection service",
        description_uz="Inkassatsiya topshirgan naqd pul bank tomonidan tasdiqlandi",
        description_ru="Банк подтвердил наличные, сданные через инкассацию",
        debit="5110.01",
        credit="5710.02",
        source_note=(
            "The closing half of the workbook's collection route, clearing "
            "5710.02 against the bank account on confirmation."
        ),
    ),
    DefaultRule(
        operation_code="cash_expense",
        description="Money paid out of the till",
        description_uz="Kassadan to'langan xarajat",
        description_ru="Расход, оплаченный из кассы",
        debit="9420",
        credit="5010",
        source_note=(
            "The workbook posts small administrative payments to 9420. Which "
            "expenses may be paid in cash at all is an organization policy "
            "question, not an accounting one."
        ),
    ),
    DefaultRule(
        operation_code="cash_shortage",
        description="Counted cash is less than expected",
        description_uz="Kassadagi naqd pul kam chiqdi",
        description_ru="Недостача наличных в кассе",
        debit="9430",
        credit="5010",
        source_note=(
            "Not in the workbook, which records no discrepancies. 9430 "
            "(other operating expenses) is the statutory counterpart. A "
            "shortage is a cost, never an adjustment to revenue."
        ),
    ),
    DefaultRule(
        operation_code="cash_overage",
        description="Counted cash exceeds what was expected",
        description_uz="Kassada ortiqcha naqd pul chiqdi",
        description_ru="Излишек наличных в кассе",
        debit="5010",
        credit="9390",
        source_note=(
            "As cash_shortage. 9390 (other operating income) — an overage is "
            "income of an unexplained kind, not extra service revenue."
        ),
    ),
    # --- purchasing (Phase 4) ---------------------------------------------
    #
    # Four of these five come straight off the workbook's own sheet. The
    # exception is `supplier_invoice_expense`, which the workbook has no row
    # for, and its source note says so.
    DefaultRule(
        operation_code="supplier_invoice_materials",
        description="Materials invoiced by a supplier — stock and a payable",
        description_uz="Yetkazib beruvchidan material uchun hisob-faktura",
        description_ru="Счёт поставщика за материалы — запасы и кредиторская задолженность",
        debit="1010",
        credit="6010",
        source_note=(
            "Workbook row 'Таъминотчидан материал олинди' posts 1010/6010. "
            "The sheet posts it on receipt; this system posts it when the "
            "invoice is registered, because the sheet has no "
            "goods-received-not-invoiced account and inventing one would be "
            "inventing a statutory classification. The amount and the "
            "liability are identical either way; only the day differs, and "
            "then only when goods arrive before their invoice."
        ),
    ),
    DefaultRule(
        operation_code="supplier_invoice_expense",
        description="A service invoiced by a supplier — an expense and a payable",
        description_uz="Yetkazib beruvchi xizmati uchun hisob-faktura",
        description_ru="Счёт поставщика за услугу — расход и кредиторская задолженность",
        debit="9420",
        credit="6010",
        source_note=(
            "Not in the workbook, which has no row for a service bought on "
            "credit — the clinic pays rent and utilities in cash and the "
            "sheet records only the cash side. 9420 is the account the sheet "
            "itself uses for administrative expense at period close, so it "
            "is the consistent choice. Needs confirmation: an accountant may "
            "want utilities and rent split out of general administration."
        ),
    ),
    DefaultRule(
        operation_code="supplier_payment_cash",
        description="A supplier paid out of the till",
        description_uz="Yetkazib beruvchiga kassadan to'landi",
        description_ru="Оплата поставщику наличными из кассы",
        debit="6010",
        credit="5010",
        source_note=(
            "The workbook pays suppliers from the bank (4310/5110.01) and "
            "has no cash row, but 5010 is its cash account throughout and "
            "the debit side is its own payable account."
        ),
    ),
    DefaultRule(
        operation_code="supplier_payment_bank",
        description="A supplier paid by bank transfer",
        description_uz="Yetkazib beruvchiga bank orqali to'landi",
        description_ru="Оплата поставщику банковским переводом",
        debit="6010",
        credit="5110.01",
        source_note=(
            "Workbook uses 5110.01 as the main current account throughout; "
            "6010 is its supplier payable."
        ),
    ),
    DefaultRule(
        operation_code="supplier_advance_bank",
        description="Paid to a supplier before any invoice — an advance held",
        description_uz="Hisob-fakturasiz yetkazib beruvchiga oldindan to'lov",
        description_ru="Аванс поставщику до счёта",
        debit="4310",
        credit="5110.01",
        source_note=("Workbook row 'Материал учун таъминотчига аванс' posts 4310/5110.01 exactly."),
    ),
    DefaultRule(
        operation_code="supplier_advance_cash",
        description="Paid to a supplier out of the till before any invoice",
        description_uz="Hisob-fakturasiz yetkazib beruvchiga kassadan to'lov",
        description_ru="Аванс поставщику наличными",
        debit="4310",
        credit="5010",
        source_note="As supplier_advance_bank, out of the till rather than the bank.",
    ),
    DefaultRule(
        operation_code="supplier_advance_applied",
        description="A supplier advance applied to their invoice",
        description_uz="Oldindan to'lov yetkazib beruvchi hisob-fakturasiga qo'llandi",
        description_ru="Аванс поставщику зачтён в счёт",
        debit="6010",
        credit="4310",
        source_note=(
            "Workbook row 'Аванс ҳисобга олинди' posts 6010/4310 exactly. "
            "Clearing an advance moves it from a receivable to a settled "
            "payable; no cash moves and no expense is recognised again."
        ),
    ),
    # --- payroll ---------------------------------------------------------
    # Every pair below is read straight off the workbook's own
    # `Учетная_политика` sheet, which already carried payroll operations
    # (rows 32-43). They are seeded as a draft like everything else: the
    # accounts are the clinic's, the approval is still an accountant's.
    DefaultRule(
        operation_code="payroll_wages_accrued",
        description="Wages accrued for the period — an expense and a debt to staff",
        description_uz="Hisoblangan ish haqi — xarajat va xodim oldidagi qarz",
        description_ru="Начислена заработная плата — расход и долг перед работником",
        debit="9420",
        credit="6710",
        source_note=(
            "Workbook row 34 'Иш ҳаққи ҳисобланиши: Администрация' posts "
            "9420/6710. Row 32 uses 2010 for staff working in main activity; "
            "a three-person clinic books its wages as administrative expense, "
            "and an accountant may add the 2010 variant if that changes."
        ),
    ),
    DefaultRule(
        operation_code="payroll_income_tax_withheld",
        description="Income tax withheld from wages — owed to the budget, not to staff",
        description_uz="Ish haqidan ushlab qolingan daromad solig'i",
        description_ru="Удержан налог на доходы из заработной платы",
        debit="6710",
        credit="6411",
        source_note=(
            "Workbook row 36 'Иш ҳаққи ҳисобланиши: Даромад солиғи' posts "
            "6710/6411. The debit reduces what is owed to the employee, "
            "which is what withholding means."
        ),
    ),
    DefaultRule(
        operation_code="payroll_inps_withheld",
        description="The INPS component moved from income tax to the pension account",
        description_uz="INPS ulushi daromad solig'idan jamg'arma hisobiga o'tkazildi",
        description_ru="Часть НДФЛ направлена на индивидуальный пенсионный счёт",
        debit="6411",
        credit="6520.02",
        source_note=(
            "Workbook row 37 'Иш ҳаққи ҳисобланиши: ИНПС' posts 6411/6520.02 "
            "— out of the income tax payable, not out of the employee's pay. "
            "This is the clinic's own evidence that the 0.1 percentage point "
            "is part of the 12% rather than an extra deduction."
        ),
    ),
    DefaultRule(
        operation_code="payroll_social_tax_accrued",
        description="Social tax on the payroll — the employer's own cost",
        description_uz="Ish haqi bo'yicha ijtimoiy soliq — ish beruvchi xarajati",
        description_ru="Начислен социальный налог — расход работодателя",
        debit="9420",
        credit="6520.01",
        source_note=(
            "Workbook row 35 'Иш ҳаққи ҳисобланиши: Администрация-ЕСП' posts "
            "9420/6520.01. It never touches 6710, which is why it can never "
            "reduce what an employee is paid."
        ),
    ),
    DefaultRule(
        operation_code="payroll_employee_pension_withheld",
        description="Employee pension contribution withheld (abolished from 2019)",
        description_uz="Xodimning pensiya badali ushlab qolindi (2019 dan bekor)",
        description_ru="Удержан пенсионный взнос работника (отменён в 2019 году)",
        debit="6710",
        credit="6520.03",
        source_note=(
            "Workbook row 43 'Иш ҳаққи ҳисобланиши: СВГПФ-8%' posts "
            "6710/6520.03. Kept for periods before 2019-01-01, when the "
            "contribution was abolished; no current run produces this line."
        ),
    ),
    DefaultRule(
        operation_code="payroll_paid_bank",
        description="Wages paid from the bank account",
        description_uz="Ish haqi bank hisobidan to'landi",
        description_ru="Выплата заработной платы через банк",
        debit="6710",
        credit="5110.01",
        source_note=(
            "Workbook row 70 'Ишчининг иш ҳаққи ҳисобидан тўловлар' posts "
            "6710/5110.01. Paying is a separate event from accruing: this "
            "settles the debt the accrual created and recognises no expense."
        ),
    ),
    DefaultRule(
        operation_code="payroll_paid_cash",
        description="Wages paid out of the till",
        description_uz="Ish haqi kassadan to'landi",
        description_ru="Заработная плата выплачена из кассы",
        debit="6710",
        credit="5010",
        source_note=(
            "As payroll_paid_bank, out of the drawer rather than the bank. "
            "The workbook pays through a transit account to cards (rows "
            "41-42); a clinic paying cash uses this."
        ),
    ),
)

DEFAULT_POLICY_NAME = "Starting policy (from the legacy workbook — needs review)"


class DefaultPolicyService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def seed(
        self,
        *,
        organization_id: uuid.UUID,
        effective_from: date,
        actor_user_id: uuid.UUID | None = None,
    ) -> AccountingPolicy:
        """Create the draft. It does not activate it.

        Refuses when a policy is already in force for `effective_from`:
        re-deriving the same rules into a second version is almost always a
        repeated command rather than an intent to supersede, and a stray
        duplicate draft is confusing to review.
        """
        policy_service = PolicyService(self._session, self._audit)
        try:
            in_force = await policy_service.policy_in_force_on(
                organization_id=organization_id, on=effective_from
            )
        except NotFound:
            in_force = None
        if in_force is not None:
            raise Conflict(
                f"Accounting policy {in_force.code} v{in_force.version} is already "
                f"in force on {effective_from.isoformat()}. To change the rules, "
                "create a new version with a later effective date.",
                details={
                    "policy_id": str(in_force.id),
                    "version": in_force.version,
                    "status": in_force.status.value,
                },
            )

        accounts = AccountService(self._session, self._audit)
        needed = {r.debit for r in DEFAULT_RULES} | {r.credit for r in DEFAULT_RULES}
        missing = [
            code
            for code in sorted(needed)
            if await accounts.get_by_code(organization_id=organization_id, code=code) is None
        ]
        if missing:
            raise ValidationFailed(
                "These accounts do not exist yet, so the starting policy cannot "
                "be built. Import the chart of accounts first.",
                details={"missing_accounts": missing},
            )

        policy = await policy_service.create_draft(
            organization_id=organization_id,
            name=DEFAULT_POLICY_NAME,
            effective_from=effective_from,
            notes=(
                "Derived from the clinic's legacy workbook. Every rule records "
                "where it came from. Review each one with an accountant before "
                "activating: see docs/OPEN_QUESTIONS.md questions A1, A4 and A5."
            ),
        )

        for rule in DEFAULT_RULES:
            await policy_service.set_rule(
                organization_id=organization_id,
                policy_id=policy.id,
                operation_code=rule.operation_code,
                description=rule.description,
                description_uz=rule.description_uz,
                description_ru=rule.description_ru,
                debit_account_code=rule.debit,
                credit_account_code=rule.credit,
                required_documents=rule.source_note,
            )

        self._audit.record(
            "accounting.policy.seeded_from_workbook",
            organization_id=organization_id,
            target_type="accounting_policy",
            target_id=policy.id,
            target_label=f"{policy.code} v{policy.version}",
            actor_user_id=actor_user_id,
            new_values={
                "rule_count": len(DEFAULT_RULES),
                "status": "DRAFT",
                "source": "legacy workbook Учетная_политика sheet",
                "needs_review": True,
            },
        )
        return policy

    @staticmethod
    def required_account_codes() -> set[str]:
        return {r.debit for r in DEFAULT_RULES} | {r.credit for r in DEFAULT_RULES}
