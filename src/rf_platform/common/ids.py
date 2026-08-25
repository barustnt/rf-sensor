from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    """Return a JSON-safe globally unique ID.

    UUID4 is used for Milestone 1 because it is in the Python standard library and stable.
    """

    return str(uuid4())
