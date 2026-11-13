"""UUIDv7 identifiers.

Every model in OneWooz uses a UUIDv7 primary key. No table has a sequential
integer key, so no identifier anywhere in the product is enumerable  you
cannot walk from order 1041 to order 1042, or from one tenant's records to
another's, by incrementing a number in a URL.

Version 7 rather than version 4 specifically because the production database is
MySQL/InnoDB, which clusters rows on the primary key. Random v4 keys scatter
inserts across the whole index and cause page splits and write amplification on
insert-heavy tables (orders, order items, analytics events). A v7 key carries a
48-bit big-endian millisecond timestamp in its leading bytes, so newly created
rows sort together and inserts stay append-like, while the remaining 74 random
bits keep the value unguessable.

Layout per RFC 9562 section 5.7::

    0                   1                   2                   3
    |unix_ts_ms (48 bits)          |ver(4)|rand_a(12)|var(2)|rand_b(62)|
"""

from __future__ import annotations

import secrets
import time
import uuid

__all__ = ["uuid7", "timestamp_of"]


def _uuid7_fallback() -> uuid.UUID:
    ts_ms = time.time_ns() // 1_000_000
    value = bytearray(ts_ms.to_bytes(6, "big") + secrets.token_bytes(10))
    value[6] = (value[6] & 0x0F) | 0x70  # version 7
    value[8] = (value[8] & 0x3F) | 0x80  # variant 0b10 (RFC 4122/9562)
    return uuid.UUID(bytes=bytes(value))


# Python 3.14 added uuid7() to the standard library. Prefer it when present so
# we inherit the interpreter's monotonicity guarantees within a millisecond.
uuid7 = getattr(uuid, "uuid7", _uuid7_fallback)


def timestamp_of(value: uuid.UUID) -> float:
    """Return the creation time of a UUIDv7 as a POSIX timestamp.

    Useful for debugging and for range queries during data forensics. Raises
    :class:`ValueError` for any other UUID version, because the leading bytes
    of a v4 are random and reading them as a timestamp would silently produce
    nonsense.
    """
    if value.version != 7:
        raise ValueError(f"expected a UUIDv7, got version {value.version}")
    return int.from_bytes(value.bytes[:6], "big") / 1000.0
