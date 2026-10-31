"""Bounded business modules.

Dependency rules, enforced by `tests/test_module_boundaries.py`:

* A module may import from `app.core` freely.
* A module may import another module's **public service and schema** modules,
  never its `models`. Cross-module reads go through a service call so the
  owning module keeps control of its invariants.
* `accounting` is the lowest business module: it may depend on `organization`
  but on nothing above it. Every other module reaches the ledger by calling
  `accounting.services.posting`, never by writing to accounting tables.
"""
