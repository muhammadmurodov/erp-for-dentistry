"""Cross-cutting infrastructure shared by every business module.

`core` must never import from `app.modules`: the dependency direction is
always modules -> core. This keeps the module graph acyclic.
"""
