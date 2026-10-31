"""The double-entry accounting engine.

The lowest business module: every other module posts *through* it and none of
them may write its tables. `tests/test_module_boundaries.py` enforces that by
failing any import of `accounting.models` from outside this package.

The invariants this module guarantees are listed in
`docs/ACCOUNTING_DESIGN.md`. Each is enforced in the service *and*, where a
database can express it, in the schema — so an application bug cannot produce
an unbalanced ledger.
"""
