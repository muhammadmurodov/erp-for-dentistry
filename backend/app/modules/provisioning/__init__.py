"""Tenant provisioning.

Creating an organization and installing its first authorised person is one
transaction that spans two modules. It lives here, above both, rather than
inside either — which is what keeps `organization` and `identity` free of a
dependency cycle.
"""
