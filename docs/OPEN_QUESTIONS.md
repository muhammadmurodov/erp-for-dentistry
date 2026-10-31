# Open questions

Decisions that need the owner or the accountant. Each says what is blocked,
what the system does meanwhile, and what evidence prompted the question.

**Nothing here is guessed in code.** Where an answer is missing, the feature
that depends on it is either not built or is explicitly configurable with no
default. This document is the authoritative list; `DECISIONS.md` records what
*has* been settled.

Legend: **Blocking** stops a specific feature. **Non-blocking** has a safe
provisional answer that is marked provisional in the system.

---

## A. Accounting and tax

### A1 — Is the derived account classification correct? · Blocking Phase 2
**Needed from:** accountant

368 accounts were extracted from the workbook's chart. The workbook's own
hierarchy column is incomplete (sections III–IX have no parent), so each
account's statutory part and accounting element are **derived from its code
range** — `0xxx` long-term assets, `1xxx–5xxx` current assets, `6xxx–7xxx`
liabilities, `8xxx` equity, and section IX split into revenue (`9000–9099`,
`9300–9399`, `9500–9599`), expense (`9100–9199`, `9400–9499`, `9600–9699`,
`9800–9899`) and result (`9200–9299`, `9700–9799`, `9900–9999`).

The derivation was cross-checked against the twelve accounts the clinic
actually posts to and agrees in every case. It still needs sign-off, because
it determines what appears as revenue versus expense on the P&L.

**Meanwhile:** every row carries `needs_confirmation: true`, and the loader
refuses a reference file that has lost its provenance notice.

**Asked as:** please confirm the section IX sub-ranges, and whether any
account the clinic uses is classified against expectation.

---

### A2 — Which statutory report forms apply, and what are their line mappings? · Blocking Phase 6
**Needed from:** accountant

The workbook contains a Form No. 1 balance sheet template with line codes
(010, 011, 012, …) mapped to account groups. It is undated and may not be the
currently required form.

**Blocked:** the statutory balance sheet and P&L. The internal trial balance
and general ledger are unaffected — they derive from the ledger directly.

**Asked as:** which forms must be filed, at what frequency, and what is the
current line → account mapping for each? A filled example of each form would
answer this completely.

---

### A3 — Which cash flow method is required for filing? · Non-blocking
**Needed from:** accountant

The system will implement the **indirect** method, because it derives entirely
from the ledger. The direct method needs every cash movement classified as
operating / investing / financing at entry time, which the clinic does not
currently record.

**Meanwhile:** indirect, with the method stated on the report itself.

**Asked as:** does statutory filing require the direct method? If so, we need
the classification rules per operation type before Phase 6.

---

### A4 — How should payments for services without an invoice be posted? · Non-blocking
**Needed from:** accountant

Two rows in the workbook's policy sheet are flagged by its own author as
"needs re-examination" (Қайта кўриб чиқиш керак): payments for services
received without a `счёт-фактура`, posted to 9430 and to 9420.

**Meanwhile:** these two rules are **not** carried into the seeded policy. The
operation types exist but are inactive, so such a payment must be classified
deliberately rather than silently following a rule the author doubted.

**Asked as:** for a service paid without an invoice, which account, and does
the treatment differ by amount or supplier type?

---

### A5 — Do the clinic's dental services bear VAT, and under which regime? · Blocking revenue posting
**Needed from:** accountant — **highest priority**

The evidence contradicts itself:

- Every fiscal receipt in 2023–2026 reports **VAT = 0**.
- The workbook nonetheless carries a check column computing a **12%**
  extraction (`ROUNDUP(price / 1.12, …)`).
- The 2025 bank statement shows **VAT paid** of roughly 2.9 million UZS, plus
  profit tax, plus a turnover-tax line.
- The 2021 worksheet computes tax as **4% of turnover** — a different regime
  entirely.

So the regime changed between 2021 and 2025, and the receipt data and the bank
data disagree about whether these services are VAT-bearing.

**Blocked:** whether a revenue posting splits into net revenue plus a VAT
liability, or posts gross. This cannot be defaulted: guessing wrong either
overstates revenue and understates a tax liability, or invents a liability
that does not exist.

**Meanwhile:** the money layer's VAT arithmetic is implemented and tested at
an illustrative rate, with the test stating that the *rate and applicability*
are unconfirmed. No posting rule uses it.

**Asked as:** what is the current tax regime (turnover tax, or VAT plus profit
tax)? Are dental services exempt, zero-rated or standard-rated? From which
date did the current treatment apply? If exempt, what are the ~2.9 M UZS of
2025 VAT payments for?

---

### A6 — What is the cut-over date, and who signs the opening balances? · Blocking go-live
**Needed from:** owner and accountant

Historical data is not imported (see `LEGACY_WORKBOOK_MAPPING.md`). The new
books open with an opening-balance journal entry at a chosen date.

**Asked as:** which date? And please supply a trial balance as at that date,
account by account, signed off. Note that part of the bookkeeping is in a
physical ledger that was unavailable during analysis, so the workbook alone
may not reconcile.

---

## B. Cash and banking

### B1 — Who may hold the cash register, and is there more than one? · Blocking Phase 3
**Needed from:** owner

The design assumes one cash register with sequential sessions, opened and
closed by an authorised cashier. Cash was ~96% of 2025 receipts by value, so
this is the clinic's main money path.

**Asked as:** how many physical registers? Who may open and close a session?
Who counts the cash at close, and who approves a discrepancy? Can the same
person do both? (If so, the system will still record both acts separately —
the segregation control is what makes the discrepancy reviewable.)

---

### B2 — What commission does each payment channel actually charge? · Non-blocking
**Needed from:** owner or accountant

The workbook carries a commission-percentage column, and the statement shows
terminal rental charged monthly and per-transaction fees. Separate transit
accounts exist for two card schemes, implying different terms.

**Meanwhile:** the fee is recorded from the actual settlement amount
(gross − received), so no rate needs to be configured for the posting to be
correct. A configured rate is only needed to *predict* a settlement and flag
an unexpected one.

**Asked as:** the commission rate per channel, and whether terminal rental is
a fixed monthly charge.

---

### B3 — In what format does the bank provide statements? · Blocking statement import
**Needed from:** owner

CSV and Excel import is built. No bank API is assumed.

**Meanwhile:** the importer does not assume a format. It discovers the
delimiter, finds the header row below a title block, reads five date formats
and three decimal conventions, and recognises the headings the clinic's own
workbook uses (ADR-035). Anything else is a `column_mapping` on the bank
account — a settings change, not a release. So import works today; what is
unverified is whether the real export needs a mapping at all.

**Asked as:** a sample export file from online banking, with its real column
headers. The workbook's columns are the owner's own layout, not the bank's.

---

### B4 — How is a bank charge on a deposit or a transfer posted? · Non-blocking
**Needed from:** accountant

The workbook establishes a fee treatment for card settlements — Dr 9420 /
Cr 5110.03 — and for nothing else. A bank that deducts a cash-handling charge
from a deposit, or a correspondent fee from an incoming transfer, would need
its own account pair.

**Meanwhile:** a withheld fee is accepted only on a card settlement, and the
refusal says so and names this question. A charge the bank takes as a
separate statement line is handled today by setting that line aside with a
reason and posting the expense as its own journal entry — which is correct
bookkeeping, just not automatic.

**Asked as:** which accounts a bank's own charge should debit and credit, and
whether it is ever netted off a deposit rather than charged separately.

---

## C. Clinic operations

### C1 — Who may read clinical treatment records? · Blocking Phase 3 clinic module
**Needed from:** owner

The design separates clinical detail from financial detail. Financial staff
get no clinical access by default; only the dentist role reads clinical
records, and such reads are audited.

**Asked as:** may a dentist read any patient's records, or only their own
patients'? May the owner read clinical records? What should be retained, and
for how long? Patient health data may carry obligations beyond ordinary
business records, and this needs a regulatory check we are not qualified to
make.

---

### C2 — Is there an appointment book to carry over? · Non-blocking
**Needed from:** owner

No appointment data appears in the workbook.

**Asked as:** is scheduling done on paper, in another system, or not formally
at all? This decides whether Phase 3 needs an import path.

---

## S. Sales and receivables

### S1 — Are partial or deferred patient payments accepted? · Blocking policy configuration
**Needed from:** owner

The receipt data shows one payment per receipt and no receivable, consistent
with payment on completion. But the system is being built to support full,
partial and deferred payment, with availability set per organization policy.

**Asked as:** may a patient pay in instalments? May treatment be delivered
before payment? Are advances taken for expensive work? Each answer switches a
policy flag; the wrong default either blocks legitimate practice or permits
receivables nobody is tracking.

---

### S2 — What is the service catalogue and who sets prices? · Blocking Phase 3
**Needed from:** owner

24 distinct service names appear in the 2025 receipts, with prices from 10 000
to 110 000+ UZS, named inconsistently across Uzbek Cyrillic and Latin.

**Asked as:** a definitive price list with one canonical name per service and
its ИКПУ (MXIK) code. Who may change a price, and does a price change apply to
treatment already started?

---

## P. Employees and payroll

### P1 — Uzbek wording for 12 statutory timesheet codes · Non-blocking
**Needed from:** accountant or HR

12 of the 25 attendance codes in the workbook carry Russian text only (codes
22, 24–29, 31–35). The Uzbek wording is statutory and is not invented here.

**Meanwhile:** the gap is declared in the data file and a test fails if it
changes without being re-declared. The interface will show the Russian text
and mark it as untranslated.

---

### P2 — Current payroll tax rules · Blocking Phase 5
**Needed from:** accountant — **high priority**

The workbook's payroll uses 2018 rates: social tax 12%/15%, SVGPF 8%, INPS,
a minimum contribution base of 65% of the 2018 minimum wage, and income tax
from a VBA function named for 2018. The VBA was not executed and **none of
these rates is carried into the new system.**

**Asked as:** for each rule in force, the current rate, its base, its
effective-from date, and any minimum or ceiling: income tax, social tax,
INPS, SVGPF. Prior versions with their date ranges are needed too, so a
recalculation of an earlier period uses the rules that applied then.

---

### P3 — How is commission-based pay calculated? · Blocking Phase 5
**Needed from:** owner

Compensation may be fixed, commission-based, or both. Commission eligibility
must be explicit, and the system will not assume two bases are equivalent.

**Asked as:** does any dentist earn a percentage? If so, of **completed
treatments** or of **collected payments**? These differ whenever a payment is
late or never arrives, and the difference is real money. What percentage, and
does it vary by service? Is commission paid on a refunded treatment?

---

### P4 — Approval chain for payroll · Non-blocking
**Needed from:** owner

The system requires approval before payroll posts, and the person who
calculates a run may not approve it.

**Meanwhile:** the HR/payroll manager role calculates; director and owner
approve.

**Asked as:** is that the intended chain? In a small clinic the owner may do
both — the system allows it for the owner role specifically, and records both
acts.

---

## I. Inventory

### I1 — Which inventory valuation method? · Blocking Phase 4
**Needed from:** accountant

**Asked as:** weighted average or FIFO for dental materials? Uzbek accounting
standards may constrain the choice; please confirm which is permitted and
which the clinic uses.

---

### I2 — Which materials need batch and expiry tracking? · Non-blocking
**Needed from:** owner

**Meanwhile:** batch and expiry are supported per product, defaulting to off.

**Asked as:** which materials require it? Should the system block the use of
expired stock, or warn?

---

### I3 — Is material consumption tracked per treatment? · Non-blocking
**Needed from:** owner

**Asked as:** is it realistic to record which materials a treatment consumed,
or is periodic stock-take the practical approach? This decides whether
consumption is a per-treatment event or a monthly adjustment.

---

## D. Operations

### D1 — Backup and retention policy · Blocking go-live
**Needed from:** owner

**Asked as:** how long must financial records be retained? (Uzbek law
prescribes a minimum; please confirm.) Where should backups be stored, and who
may restore? A restore must be *tested*, not merely configured.

---

### D2 — Who operates the system? · Non-blocking
**Needed from:** owner

**Asked as:** who holds the owner account? Who is called when something
breaks? The owner account can grant any permission, so this is a security
decision as much as an operational one.
