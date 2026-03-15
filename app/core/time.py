from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """
    Return current UTC time as a naive datetime.

    DB columns currently use TIMESTAMP WITHOUT TIME ZONE; this keeps storage semantics
    as UTC while avoiding direct utcnow() usage.
    """
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)
