# Evidence and gaps

For every question that is still open, five statements:

1. **What the workbook proves** — established from the clinic's own file,
   not assumed.
2. **What the code already implements** — built, tested, and reachable.
3. **What is genuinely missing** — the part no amount of reading the
   evidence can supply.
4. **Can implementation proceed safely?**
5. **Must posting remain blocked?**

The distinction between 4 and 5 is the point of this document. Most of these
questions block a *posting rule*, not a *feature*. Where that is so, the
feature ships, the posting refuses, and the refusal names the question.

**Source of the evidence.** `WORKBOOK_FORMULA_AUDIT.md` (2026-11-04), which
read the workbook twice with `openpyxl`, decompressed the VBA rather than
executing it, and never modified the original. Its MD5 is recorded there.
**No employee name, individual salary or patient record appears here**, and
none is reproduced in code, fixtures, tests or logs.

**Last updated:** 2026-11-09.

---

## I1 — Inventory valuation · **Decided 2026-11-09: moving weighted average**

1. **The workbook proves** what every material cost, line by line, through
   the supplier invoices it records. It does **not** contain a declared
   valuation method — no FIFO layers, no standard costs, no stated policy.
2. **The code implements** the quantity ledger in full (receipts,
   consumption, adjustments, write-offs, transfers, batches, expiry), and a
   weighted-average cost derived from registered supplier bills in
   `purchasing/services/stock_valuation.py`. FIFO is explicitly refused
   there, because the cost layers it needs are not kept.
3. **Missing:** nothing further for valuation itself. The owner's
   declaration was the missing input and it has now been given.
4. **Implementation can proceed.** See the note below on what remains.
5. **Posting stays blocked** until the valuation method is recorded on the
   organization *and* the accounting policy carrying the consumption rule is
   approved. Valuing stock for a screen and posting the cost of a material
   to the ledger are two different acts; the first needs a method, the
   second needs an approved rule as every posting does.

> **Still to build for I1:** the method is currently `UNDECLARED` on every
> organization and there is no endpoint to set it, and the existing average
> is a *period* average over all registered bills rather than a *moving*
> one recomputed at each receipt. Both are in progress.

---

## A5 — VAT on dental services · **Posting stays blocked**

1. **The workbook proves**, from the fiscal receipt sheets, that recorded
   VAT changed from zero to 12% **VAT-inclusive on 2025-04-01**: 298 receipt
   lines in 2025-01…03 carry zero on every line, and 755 lines from 2025-04
   onward carry exactly 12.00%, computed per line as
   `price − ROUNDUP(price ÷ 1.12, 2)`. It also proves the 2021 worksheet
   computed tax as 4% of turnover — a different regime entirely.
2. **The code implements** the VAT-inclusive arithmetic and its per-line
   rounding, tested at an illustrative rate, with the test stating that the
   rate and applicability are unconfirmed. Invoices issue under the "no tax
   split" treatment, and the revenue tax treatment is a per-organization
   setting with no default.
3. **Missing:** *why* the change happened — a turnover threshold crossed, or
   voluntary registration — which decides whether 12% is still right going
   forward and what the correct treatment of the three VAT-free months of
   2025 is. The receipt data is a record of what was charged; it is not a
   ruling on what is owed.
4. **Implementation can proceed**, and has: the arithmetic, the per-line
   rounding and the setting all exist.
5. **Posting must remain blocked.** Guessing wrong either overstates revenue
   and understates a tax liability, or invents a liability that does not
   exist. The evidence narrows the question to one an accountant can answer
   in a sentence; it does not answer it.

---

## P2 — Current payroll statutory rates · **Posting stays blocked**

1. **The workbook proves** that 12% employer social tax was applied through
   2026-08, and that this is historically consistent: 15% before 2019-01,
   12% after, matching the confirmed statutory reduction. It also proves the
   employee-side history — 8% pension contribution ending 2019-01, income
   tax becoming a flat 11.9% + 0.1% INPS — each independently confirmed
   against a published source in the audit's legal table.
2. **The code implements** statutory rates as dated, approved rules carrying
   a legal reference, payslips that keep the rule version that governed
   them, and a payroll run that calculates and is approved but **refuses to
   post** without approved rules. The person who calculates cannot approve.
3. **Missing:** confirmation that 12% is the rate *now*. The audit could not
   verify 2025–2026 from an authoritative source, and one secondary source
   reports a figure that matches nothing in the workbook.
4. **Implementation can proceed** and is complete; what the clinic applied
   historically is recorded as **evidence**, not seeded as an approved rule.
5. **Posting must remain blocked** until an accountant approves the rate as
   a dated rule. This is the control working, not a gap: a run that posts on
   an unapproved rate is a filing nobody authorised.

---

## D1 — Retention and backup · **Destruction blocked; the rest is built**

1. **The workbook proves** nothing about retention; it is a file, not a
   policy.
2. **The code implements** the whole document module around the safe
   direction of this uncertainty: nothing is ever deleted, every file's
   SHA-256 is recorded at upload and re-checked on every download, a failed
   check refuses the download and is recorded in its own transaction so it
   survives the failed request, `verify-documents` re-checksums the entire
   store against the database and exits non-zero on any mismatch, and files
   on disk that no row claims are reported rather than removed.
3. **Missing:** how long each kind of record must be kept, where backups
   live, and who may restore.
4. **Implementation proceeded** and is live.
5. **No posting is involved.** What stays blocked is *destruction*: there is
   no delete endpoint, and there will not be one until a retention schedule
   exists.

---

## B3 — Bank statement format · **Import works; one unknown remains**

1. **The workbook proves** the column headings the clinic's own bank lines
   carry, across the three languages they arrive in, in `BankAylanmasi`.
2. **The code implements** a per-account column mapping seeded from exactly
   those headings, with Cyrillic, Latin and Uzbek variants, five date
   formats, duplicate refusal on re-import, and footer-total rows
   distinguished from rows the mapping genuinely failed on.
3. **Missing:** whether a real export from the bank's own system matches the
   workbook's transcription. The workbook is a *copy* of statement data into
   a spreadsheet; the bank's file may carry different headings, encodings or
   a preamble.
4. **Implementation can proceed** and has; the mapping is configurable per
   account precisely because the first real file may differ.
5. **Nothing is blocked.** Importing posts nothing. A mismatch shows up as
   unmapped rows, which are reported rather than silently dropped.

---

## A2 — Statutory report forms · **Management reports ship; forms blocked**

1. **The workbook proves** two things that matter more than the template it
   contains. Its `Balance` sheet has **one formula in 120 rows** — the
   statutory balance sheet was typed, not derived — and `Бош_китоб`, the
   general ledger, carries 88 formulas for a business with thousands of
   transactions, with amounts entered as literals. The Form No. 1 template
   it holds is **undated**.
2. **The code implements** every management report the clinic runs — profit
   and loss, balance sheet, receivables and payables ageing, cash movement,
   trial balance, account statement — each deriving from the ledger with
   comparative periods, drill-down and spreadsheet export.
   `GET /reporting/catalogue` reports the statutory forms as blocked and
   names this question.
3. **Missing:** which forms must be filed, at what frequency, and the
   current line → account mapping for each. A filled example of each form
   answers this completely. The workbook's undated template cannot, because
   nothing in it says whether it is still the required form.
4. **Implementation can proceed** for everything internal, and has.
5. **Nothing is posted** by a report. What is blocked is *producing a
   statutory form*, which would otherwise be a filing built on a mapping
   nobody confirmed.

---

## P5 — Goods received before their invoice · **Accrual blocked**

1. **The workbook proves**, by absence, that its chart carries **no
   goods-received-not-invoiced account** and its 85 policy operation types
   contain no such rule. Absence from a chart this detailed is evidence the
   clinic did not account this way.
2. **The code implements** the four purchasing states and posts them
   correctly: an order posts nothing, a delivery posts nothing, the supplier
   invoice is what reaches the ledger. What has arrived and not yet been
   invoiced is reported as a figure on screen.
3. **Missing:** whether the accrual is required at all at this scale, and if
   so which account carries it. Inventing an account in a statutory chart is
   not a technical decision.
4. **Implementation can proceed** — the quantity and the exposure are both
   already visible.
5. **The accrual posting stays blocked.** The gap is real, bounded and
   reported rather than papered over.

---

## A7 — Account for a written-off patient debt · **Posting blocked**

1. **The workbook proves** that a cash shortage is posted to 9430, other
   operating expenses. It proves nothing about an uncollectable patient
   debt, which does not appear in its policy sheet.
2. **The code implements** the write-off as a distinct operation —
   registered, permissioned, and deliberately **without a policy rule** — so
   an attempt fails loudly instead of posting to a guessed account. A test
   asserts that failure rather than skipping it.
3. **Missing:** which account carries the debt, and whether a provision is
   required first or a direct write-off is acceptable at this scale. `9430
   Dr / 4010 Cr` is the proposal by analogy with the shortage treatment; an
   analogy is not an authorisation.
4. **Implementation can proceed** and is complete up to the posting.
5. **Posting must remain blocked.**

---

## The remainder, in one line each

| Question | Can build? | Posting blocked? | What is missing |
| --- | --- | --- | --- |
| A1 account classification | Yes, built | No | Sign-off on the derived section IX ranges |
| A3 cash flow method | Yes, built (indirect) | No | Whether filing requires the direct method |
| A4 service paid without an invoice | Rules exist, inactive | Yes | Which account; the workbook's own author doubted the two rows |
| A6 opening balances | Import not built | Yes | The balances themselves, and who signs them |
| B4 bank charge outside a card settlement | Yes | Yes | The posting rule |
| P1 twelve statutory timesheet codes | Own five codes built | No | The official Uzbek wording |
| P3 commission pay | No | Yes | How commission is calculated |
| P6/P7/P8/P9/P10 historical payroll anomalies | Reproducible as history | Yes for re-computation | Why each anomaly happened; P10's macro lives in a file not supplied |
| C-series clinic questions | Built where answerable | No | See `OPEN_QUESTIONS.md` |

---

## What this means in practice

Of the questions above, **three block a feature** (A2's statutory forms,
P3's commission pay, A6's opening-balance import) and the rest block a
**posting rule** behind a feature that already works. That is the pattern
worth keeping: build the thing, refuse the entry, and name the question in
the refusal — so the day an accountant answers, the change is a row in a
policy table rather than a release.
