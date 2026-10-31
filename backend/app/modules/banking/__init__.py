"""Bank accounts, statement import and reconciliation.

Sits above `sales` and beside `cash`: a bank settlement matches payments and
a statement line confirms a deposit, so this module knows about both and
neither knows about it.

The distinction this module exists to keep is the one the specification is
emphatic about: a patient payment, a fiscal receipt, a card-processor
settlement, a bank statement line and an accounting entry are **stages of one
economic transaction**, not five separate sources of revenue. Matching a
statement line to a payment records a reconciliation fact; it does not
recognise income a second time.
"""
