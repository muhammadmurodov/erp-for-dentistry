"""Cash registers, sessions and physical reconciliation.

Sits above `sales`: a session *contains* payments, so the container knows its
contents rather than the reverse (ADR-030). Posts through
`accounting.services.posting` and never writes a ledger table.

The point of this module is the daily close. Expected cash is computed from
recorded movements and is never an editable field; the counted amount is what
a person physically counted; the difference between them is recorded, not
absorbed.
"""
