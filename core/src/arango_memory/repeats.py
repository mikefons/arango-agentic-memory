"""Counting writes that recognise being run twice.

After a broken connection the driver sends a request again, even one the database has already
run (`transport`). A write that adds to a count would then add twice. So each carries an id of
its own, made once per write and therefore the same on a resend, and the document keeps the
last few it has applied: seen again, the update changes nothing. In AQL:

    UPDATE (@op IN NOT_NULL(OLD.ops, [])) ? {} : { ..., ops: SLICE(PUSH(NOT_NULL(OLD.ops, []),
        @op), -16) }

Only the last 16 are kept: a resend follows at once, so what matters is that fewer than 16
other writes reach the same document in between.
"""

from __future__ import annotations

import uuid


def new_op() -> str:
    return uuid.uuid4().hex[:16]
