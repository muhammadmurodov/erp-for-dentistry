"""Clinic: patients, appointments, encounters and treatment records.

Sits above sales in the module layering. It reads and writes money only
through `BillingService` and `CatalogueService`, never by touching their
tables, so there is one receivables ledger in the system and not two.
"""
