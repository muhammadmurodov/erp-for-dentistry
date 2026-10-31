# Reference data

Statutory reference material extracted from the legacy workbook, for seeding
an organization's initial configuration.

| File | Contents | Confirmation required |
| --- | --- | --- |
| `uz_chart_of_accounts.json` | The Uzbek national chart of accounts (NSBU): 6 parts, 8 sections, 75 groups, 279 accounts. | Yes — `statutory_part`, `element` and `range_label` are derived from code ranges, not read from the source. See OPEN_QUESTIONS A1. |
| `uz_timesheet_codes.json` | The statutory timesheet (tabel) attendance codes, Uzbek and Russian. | Yes — see OPEN_QUESTIONS P1. |

## What is deliberately absent

The legacy workbook also contains the clinic's own bank account numbers, its
director's name, employee salary history, patient-level fiscal receipts and
the MFO directory of every Uzbek bank branch. None of it is here. These files
hold only material that is public legislation.

Nothing in this directory is loaded automatically. A Phase 2 seeding command
will read it on request, so that importing a 368-row chart of accounts is an
explicit, auditable act rather than a migration side effect.
