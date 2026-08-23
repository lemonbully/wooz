"""Shared infrastructure for OneWooz.

Nothing in this package knows about sites, products or orders. It provides the
primitives every other app is built on: identifiers, tenant scoping, signed
tokens, field encryption, rate limiting, auditing and host routing.
"""
