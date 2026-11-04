# Workbook formula audit

Every calculated field in the clinic's Excel workbook: what it computes, what
it depends on, which period it governed, whether it is policy-dependent, and
what the ERP should do about it.

**This is the deliverable that precedes implementation.** Nothing in the
payroll module is built from a formula in here until its business meaning and
its governing policy are both established. Where they are not, the finding is
recorded as a question for the accountant rather than coded as a rule.

**The workbook is evidence of historical practice, not a specification of
current law.** A rate that was right in 2018 is not thereby right today, and
a rate that changed is not thereby an error.

## Method and provenance

| | |
| --- | --- |
| File | `Kichik tadbirkorlik subektlari uchun buxgalteriya- ver 10-2024-01-10.xlsm` |
| MD5 | `dfa34c91b550d7daa4c7a5c65b43ed1e` |
| Read | `openpyxl`, twice: once for formulas, once for the values Excel last cached |
| Macros | **Never executed.** The VBA project was decompressed and read as text |
| Original | **Never modified.** All analysis ran against a read-only copy |
| Date | 2026-11-04 |

Reading the VBA needed a compound-file reader and the MS-OVBA decompressor,
because opening the file in Excel would have run its macros. The project
contains exactly one function, `RegExpExtract`, and otherwise only empty
sheet modules — which is itself a finding, below.

**Privacy.** No employee name, individual salary or patient-level record
appears in this document. Amounts are quoted only where they size a finding.

## Formula census

31,161 formulas across 23 sheets. Where they are:

| Sheet | Formulas | What is calculated |
| --- | --: | --- |
| `hisobraqam` | 5,170 | Chart-of-accounts hierarchy: level and nature lookups |
| `BankAylanmasi` | 19,442 | Bank lines reconciled against the cash report |
| `Kassa 2025` / `2026` / `2023` | 1,923 / 173 / 1,068 | Fiscal receipts: VAT extraction and cross-check |
| **`Иш_ҳаққи`** | **2,345** | **Payroll, monthly per employee, 2018-01 to 2026-08** |
| `ПлатежкиБанк_UZS` | 659 | Payment orders: amount in words, totals |
| `Учетная_политика` | 245 | Accounting policy: 85 operation types → debit/credit pair |
| `Бош_китоб` | 88 | General ledger: account pair looked up from the policy |
| `Пенсия_справкаси` | 22 | Pension certificate: the 8% contribution, per month |
| `Иш_ҳақи_справкаси-*` | 18 | Salary certificate for one individual |
| `2021-hisobotlari` | 4 | Declared-versus-actual revenue reconciliation |
| `Balance` | **1** | Statutory balance sheet, Form No. 1 |
| Others (`tabel`, `bank`, `Маълумотлар`, pivots) | 0 | Reference data and pivot summaries |

Two things in that table are findings in themselves. The statutory **balance
sheet has one formula in 120 rows** — it is typed, not derived. And the
**general ledger has 88 formulas for a business with thousands of
transactions** — it summarises rather than records.

## The audit matrix

Risk is the risk of carrying the calculation into the ERP unexamined.

### Payroll — `Иш_ҳаққи`

| Cell / range | Existing formula | Business meaning | Dependencies | Period | Policy-dependent? | Risk | ERP implementation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `I8:I217` | `=E/F*D` | Gross pay prorated by days worked | D salary, E worked days, F working days | 2018-01 → 2026-08, unchanged | No — arithmetic | Low | **Reproduce.** This is the 13-of-26 rule in the brief |
| `H10:H217` | `=J{prev}` | Cumulative income brought forward | previous row's J | throughout | No | Medium — a row order dependency | **Replace** with a query over the year's approved runs |
| `H104`, `H177`, `L104`, `M104`, `L177`, `M177` | `=J{prev}*0`, `=L{prev}*0+$N` | Year-start reset of cumulative totals | — | 2022-01, 2024-01 | No | **High** — a reset implemented by multiplying by zero | **Replace** with a fiscal-year boundary |
| `J8:J217` | `=$H+$I` | Cumulative income after this accrual | H, I | throughout | No | Low | Reproduce as a derived figure |
| `K8:K31` | `=com.microsoft.single(daromadsoligi2018($B,$J))` | 2018 progressive income tax, from a macro | **a VBA function that is not in this workbook** | 2018 only | Yes | **Critical — evaluates to `#NAME?`** | **Do not port.** Historical 2018 tax must be re-derived or entered as approved historical fact |
| `L8:M31` | `=VALUE(LEFT(K,FIND("/",K)-1))` etc. | Splits the macro's `"tax/inps"` string | K | 2018 only | No | **High** — a number passed as text | Not portable; the shape itself is the defect |
| `N33:N55` | `=$I*12%` | Current month income tax | I | 2019 only, **one employee** | Yes | **High — 12% charged beside a separate 0.1% INPS** | Confirmed defect, below |
| `N32:N217` | `=$I*(12%-0.1%)` | Income tax at 11.9%, INPS taken separately | I | 2019-01 → 2026-08 | Yes | Low — matches the law | **Reproduce as a versioned rule** |
| `O32:O217` | `=$I*0.1%` | INPS, the 0.1 pp component of the 12% | I | 2019-01 → 2026-08 | Yes | Low | **Reproduce as a versioned rule** |
| `N128:N151`, `O128:O151` | `=$I*(12%-0.1%)*0` | Income tax and INPS **zeroed** | — | 2023-01, 2023-10, 2023-12 | Unknown | **Critical — unexplained** | **Hold.** Question P6 |
| `N130`, `O130` | `=($I-1744490)*(12%-0.1%)` | Tax on income less a fixed 1,744,490 | I | 2023-02, one employee | Unknown | **Critical — unexplained** | **Hold.** Question P6 |
| `P8:P31` | `=$I*8%` | Employee insurance contribution to the pension fund | I | 2018 only | Yes | Low — correct for 2018 | Historical rule only; **abolished 2019-01-01** |
| `P32:P217` | `=$I*0%` | The same contribution, switched off | — | 2019-01 → | Yes | Low | Reproduce as a rule that ends, not a zero |
| `Q8:Q31` | `=$T+$S` | 2018 social payment: 15% plus top-up to the minimum | T, S | 2018 | Yes | Medium | Historical rule |
| `T8:T31` | `=$I*15%` | Social payment at 15% | I | 2018 | Yes | Low | Historical rule |
| `R8:R31` | `=172240*65%` | Minimum social-payment base: 65% of the 2018 minimum wage | **hardcoded 172,240** | 2018 | Yes | **High — a statutory constant typed into a formula** | **Replace** with a dated constant |
| `U8:U31` | `=$E/$F*(65%*172240)` | That minimum, prorated by days worked | E, F, hardcoded | 2018 | Yes | High — same constant again | Replace |
| `S8:S31` | `=IF($U>$T,$U-$T,0)` | Top-up when 15% falls below the minimum base | T, U | 2018 | Yes | Low | Reproduce as a rule shape |
| `Q32:Q217` | `=$I*12%` | Social tax, employer | I | 2019-01 → 2026-08 except below | Yes | Medium — **current rate unverified** | **Hold for the current period.** Question P2 |
| `Q64:Q79` | `=$I*1%` | Social tax at **1%** | I | **2020-05 → 2020-12** | Yes | **High — unverified** | **Hold.** Question P7 |
| `V8:V217` | `=$I-$N-$O-$P` | Net pay | I, N, O, P | throughout | No | **Low — and correct** | **Reproduce.** Note what is *absent*: Q, the employer's social tax, is correctly not deducted |
| `V37` | `=$I-$N-$O-$P+SUM(AA37,Z37)` | Net pay plus two typed corrections | Z37, AA37 | 2019-03 | No | **High — undocumented** | **Hold.** Question P8 |
| `Z13`, `Z19:Z31`, `Z37`, `AA37`, `Y11:Y13`, `Y74` | `=8555.2-1755.2`, `=(18662.4-41337.96)`, `=51321.6-132340.79`, `=$V-119908.6`, `=$V-446000` | Manual corrections, no stated reason | hardcoded | 2018–2019, 2021 | Unknown | **High** | **Hold.** Question P8 |
| `W8:W31` | `=$O*100%/2%` | "Income in the INPS report" — grosses O up at 2% | O | 2018 | Yes | **High — 2% here, 0.1% in column O** | **Hold.** Question P9 |

### Accounting policy — `Учетная_политика`

| Cell / range | Existing formula | Business meaning | Dependencies | Period | Policy-dependent? | Risk | ERP implementation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `C3:D102` | typed `ACC-nnnn` | 85 operation types, each with a debit and a credit account | — | undated | Yes | Medium — no effective dates anywhere | **Already reproduced** as the versioned posting policy, seeded as a draft |
| `E3:F102` | `=IFNA(INDEX(hisobraqam!$G:$G,MATCH($C,hisobraqam!$B:$B,0)),"-")` | Account names, for reading | `hisobraqam` | undated | No | Low | Reproduce as a join |
| `I3:I102` | `=$C&" / "&$D` | The pair as one string, used as a filter key | C, D | undated | No | Low | Not needed; the ERP holds the pair |

### General ledger, balance sheet and certificates

| Sheet · cell | Existing formula | Business meaning | Dependencies | Period | Policy-dependent? | Risk | ERP implementation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Бош_китоб` `F13:G59` | `=INDEX(Учетная_политика!$C:$C,MATCH($E,…!$B:$B,0))` | The posting pair for each ledger line, from the policy | policy sheet, E operation code | 2017 → | Yes | Low — the right idea | **Already reproduced**: this *is* policy-driven posting |
| `Бош_китоб` `F18:G59` (42 cells) | same, over a blank operation code | — | — | — | No | Low | `#N/A` from empty template rows, not missing mappings |
| `Бош_китоб` `H28:H30` | `=981000+1094700` | A ledger amount, typed as a sum | hardcoded | — | No | **High** | Amounts must come from transactions |
| `Бош_китоб` `H32` | `=3900000*0.2/12*12` | Depreciation: cost × 20% ÷ 12 × 12 months | hardcoded | — | Yes | Medium — intent is sound, written opaquely | Asset accounting, not built |
| `Бош_китоб` `H35` | `=3559981.77` | A typed amount | — | — | No | High | As above |
| `Balance` `E8` | `=SUMIFS(Бош_китоб!$H,Бош_китоб!$F,"=ACC-5111")` | One balance-sheet line, from the ledger | ledger | — | Yes | Medium | **Already reproduced** — and generalised: every line now derives |
| `Balance` rest of 120 rows | typed numbers | The statutory balance sheet | — | — | Yes | **Critical — a statement that does not derive from the books** | Reproduced as a derived statement (ADR-057) |
| `Пенсия_справкаси` `E12:E23` | `=D*0.08` | The 8% contribution, per month, for the pension certificate | D monthly wage | **2018 column only** | Yes | Medium — correct then, wrong if reused | Certificate generation; the rate must come from the dated rule |
| `Пенсия_справкаси` `C12:C23` | typed | The same for 2017 | — | 2017 | Yes | Medium — **does not equal 8% of column B** | Hold; 2017 figures were entered by hand |
| `2021-hisobotlari` `B3` | `=SUM(B1:B2)` | Declared revenue reconciliation | typed totals | 2021 | No | Low | Superseded by the reports module |
| `hisobraqam` `X4` | `=#REF!-#REF!` | — | nothing | — | No | Low — isolated | Discard |

### Fiscal receipts — `Kassa 2025`, `Kassa 2026`

| Cell / range | Existing formula | Business meaning | Dependencies | Period | Policy-dependent? | Risk | ERP implementation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `V3:V963` | `=ROUNDUP([Нархи]/1.12,2)` | Price excluding VAT, at 12% | price | 2025 → | **Yes** | **Critical — see below** | **The evidence that settles `A5`** |
| `W3:W963` | `=ROUNDUP([QQS siz summa]+[ҚҚС суммаси],2)` | Check: net plus VAT should equal the price | V, fiscal VAT column | 2025 → | No | Medium — **the check fails for 2025-01..03** | The ERP reconciles instead |

## Findings, classified

### Confirmed calculation defects

**D1 · The whole of 2018's payroll tax is `#NAME?`.** Column K calls
`daromadsoligi2018(…)`, a VBA function that **is not in this workbook** — the
project contains only `RegExpExtract`. 175 cells on the payroll sheet carry
`#NAME?`, and because net pay depends on them, **2018 net pay does not
currently compute at all**. The workbook's defined names point at a different
file on OneDrive (`…/Fazli akam/Kichik tadbirkorlik subektlari uchun
buxgalteriya.xlsm`), including a name `МРЗП_2017` pointing at a sheet called
`Подоход` — so the function almost certainly lives there. Until that file is
produced, 2018's figures exist only as whatever was filed at the time.

**D2 · One employee was taxed at 12.1% through 2019.** Column N used
`=I*12%` for one of the two employees in every month of 2019, while column O
charged INPS at 0.1% separately — so 12.1% was withheld where the law takes
12%, of which 0.1 pp *is* the INPS. The other employee's rows used
`=I*(12%-0.1%)` and were right. Over-withheld: **3,836.07 UZS** across twelve
months. Small, and unambiguous: two rows of the same month, computed two
different ways.

**D3 · A cumulative reset done by multiplying by zero.** Year-start rows
carry `=J{prev}*0` and `=L{prev}*0+$N`, so the reset depends on somebody
remembering to edit one cell each January. 2022 and 2024 have it; the pattern
is not applied consistently across every year boundary.

**D4 · `hisobraqam!X4` is `=#REF!-#REF!`.** One cell, no dependants.

### Potential defects requiring investigation

**P9 · The INPS report column contradicts the INPS column.** `W = O*100%/2%`
grosses the contribution up at **2%**, while `O` computes it at **0.1%**.
Both cannot describe the same contribution. 2018 only.

**The general ledger is typed, not derived.** `=981000+1094700`,
`=3559981.77` and similar put amounts into the ledger by hand. The pairing of
accounts is automated; the money is not.

**The statutory balance sheet is typed.** One formula in 120 rows.

### Legitimate historical variation — not errors

**V1 · The 8% employee pension contribution stops after 2018.** Column P goes
from `=I*8%` to `=I*0%` at 2019-01. That is the law changing, not a mistake:
the 8% insurance contribution from citizens' wages was **abolished from 1
January 2019**.

**V2 · Income tax becomes a flat 11.9% + 0.1% from 2019.** The progressive
2018 scale (a macro) is replaced by `=I*(12%-0.1%)` and `=I*0.1%`. This
matches the reform exactly: a single 12% rate from 1 January 2019, of which
0.1 pp is routed to the individual pension account.

**V3 · Social tax falls from 15% to 12% at 2019-01.** Confirmed: the rate for
non-budget payers was reduced from 15% to 12%.

**V4 · The 2018 minimum social-payment base.** `65% × 172,240` is the 2018
minimum wage with the 65% coefficient — correct for its period, and correctly
prorated by days worked in column U.

### Unresolved policy questions — for the accountant

These are recorded as new entries in `OPEN_QUESTIONS.md`.

**P6 · Why was income tax zeroed in three months of 2023?** 2023-01, 2023-10
and 2023-12 have tax and INPS multiplied by zero for both employees, and
2023-02 applies a deduction of exactly 1,744,490 to one. Together these
withhold **1,537,338 UZS less** than 12% would. The `*0` is deliberate —
somebody typed it — so this is either an exemption, a correction of earlier
over-withholding, or an error. It cannot be told from the sheet.

**P7 · Why was social tax 1% from May to December 2020?** Column Q drops from
12% to 1% for eight months and returns to 12% in January 2021. A pandemic
relief measure is the obvious hypothesis and **it could not be confirmed from
any authoritative source** in this audit. Unverified, so unimplemented.

**P8 · Nine undocumented manual corrections.** Hardcoded differences typed
into columns Y, Z and AA — `=8555.2-1755.2`, `=(18662.4-41337.96)`,
`=51321.6-132340.79`, `=$V-119908.6`, `=$V-446000`. One of them reduces a
2019-03 net pay by **103,694.75 UZS**. None carries a reason.

**P2 (existing) · What is the social tax rate now?** The workbook applies
12%. The audit could not confirm the rate for 2025–2026 from an authoritative
source, and one secondary source reports an employee social security rate of
4% for December 2025, which does not match anything in the workbook. **The
current rate must be confirmed before any payroll is posted.**

### Safe calculations to preserve

- **Proration**: `gross = worked ÷ working × salary`. Unchanged for eight
  years and arithmetically sound.
- **Net pay**: `net = gross − income tax − INPS − employee pension`. The
  employer's social tax is **correctly excluded**. The workbook never makes
  the mistake the brief warns about.
- **The minimum-base top-up shape**: `IF(minimum > rate × base, difference, 0)`.
- **Policy-driven posting**: operation type → debit/credit pair, looked up
  rather than typed. This is the idea the ERP already implements.

## Legal verification

| Rule | Effective | Status | Source |
| --- | --- | --- | --- |
| Personal income tax: single 12% rate | from 2019-01-01 | **Confirmed** | [kun.uz](https://m.kun.uz/en/news/2018/12/25/income-tax-for-individuals-to-be-set-at-12-from-january-1-2019), [kun.uz 2020](https://kun.uz/en/news/2020/01/07/income-tax-rates-for-individuals-remain-unchanged) |
| INPS: 0.1% of taxable wage, a component of the 12% | from 2019-01-01, still 2026-02 | **Confirmed** | [buxgalter.uz 2022](https://buxgalter.uz/en/publish/doc/text185116_kak_rasschitat_ndfl_i_inps_za_yanvar_2022_goda), [buxgalter.uz 2026-02](https://buxgalter.uz/publish/doc/text213796_kak_rasschitat_ndfl_i_vznosy_na_inps_za_fevral_2026_goda) |
| Employee 8% pension insurance contribution abolished | from 2019-01-01 | **Confirmed** | [buxgalter.uz](https://buxgalter.uz/publish/doc/text146767_esp_i_strahovye_vznosy_grajdan_-_chto_novogo_v_2019_godu), [Deloitte tax alert](https://www.deloitte.com/content/dam/Deloitte/kz/Documents/uzbekistan/UZ_SPECIAL%20_Tax_AlertDecember%202018_EN.pdf) |
| Social tax reduced 15% → 12% for non-budget payers | from 2019-01-01 | **Confirmed** | [Deloitte tax alert](https://www.deloitte.com/content/dam/Deloitte/kz/Documents/uzbekistan/UZ_SPECIAL%20_Tax_AlertDecember%202018_EN.pdf), [spot.uz](https://www.spot.uz/ru/2018/12/26/tax2019/) |
| Minimum wage 1,155,000 → 1,271,000 | 2024-10-01 / 2025-08-01 | **Confirmed** | [buxgalter.uz](https://buxgalter.uz/publish/doc/text215814_rasschitat_ndfl_i_vznosy_na_inps_za_iyun_2026_goda) |
| Social tax at 1%, May–December 2020 | — | **Not confirmed** | No authoritative source found |
| Social tax rate for 2025–2026 | — | **Not confirmed** | Must be confirmed with the Tax Committee |
| Why income tax was zero in three months of 2023 | — | **Not confirmed** | Clinic's own records |

Rates in this table are what the sources say on the dates given. They are
recorded as evidence for an accountant to confirm, not as a basis for
calculating anybody's pay.

## What this changes for the ERP

**The clinic began charging VAT on 1 April 2025.** This is the audit's most
consequential finding and it bears directly on `OPEN_QUESTIONS.md` **A5**,
which currently blocks every revenue posting:

| Period | Receipt lines | VAT on them | Implied rate |
| --- | --: | --- | --- |
| 2025-01 → 2025-03 | 298 | **zero on every line** | 0% |
| 2025-04 → 2026-01 | 755 | **every line** | **exactly 12.00%** |

The receipts compute VAT as `price − ROUNDUP(price ÷ 1.12, 2)`, per line —
VAT is *included* in the displayed price, which is precisely the
`VAT_INCLUSIVE` treatment the ERP defines and refuses to apply while A5 is
open. The clinic's own fiscal data now answers the factual half of A5: dental
services here bear 12% VAT, from 2025-04-01. What it does not answer is
**why** — a turnover threshold crossed, or voluntary registration — and the
accountant must confirm that before the treatment is switched on, because it
decides whether the rate is right going forward and what happens to the three
VAT-free months of 2025.

Two further consequences:

1. **Per-line rounding matters.** A month's VAT computed from the monthly
   total differs from the sum of per-line figures: for 2025-04, 707,678.57
   against the receipts' 707,678.26. The ERP must extract VAT per line, with
   the same rounding, or it will disagree with the fiscal module.
2. **The posting policy needs a VAT rule.** The 85 operation types in
   `Учетная_политика` carry no VAT account, because they predate this.

## Recommended order of implementation

Only the first item is unblocked end to end.

1. **The versioned payroll policy, and the calculations whose law is
   confirmed**: proration, the 11.9% + 0.1% split from 2019-01-01, the 8%
   contribution ending 2019-01-01, the employee/employer separation, and the
   2018 minimum-base shape. Each statutory rate becomes a dated, approved,
   referenced record — never a literal in code.
2. **Everything else in payroll is gated** on P2 (current social tax rate),
   P6, P7, P8 and P9. A payroll run that needs an unapproved statutory rule
   must refuse to post and name the rule, exactly as a missing posting rule
   does today (ADR-051).
3. **Historical payroll is reproducible, not recomputed.** 2018–2025 runs are
   entered as approved historical fact with the rule version that governed
   them. A new policy must never reach back into an approved run.
4. **A5 goes back to the accountant with this evidence attached.** The ERP
   stays on the strict reading until they answer.
