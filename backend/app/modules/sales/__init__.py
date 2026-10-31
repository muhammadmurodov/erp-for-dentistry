"""Sales, invoicing and accounts receivable.

Owns the commercial side of a transaction: what was sold, what was billed,
what was paid, and what is still owed. It posts through
`accounting.services.posting` and never writes a ledger table.

The distinction this module exists to keep is that **service completion,
invoice issuance, revenue recognition and payment collection are four
different events**. Conflating them is how a business ends up counting the
same money more than once — see `docs/BUSINESS_PROCESSES.md`.
"""
