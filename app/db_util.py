"""Small database helpers."""
import logging

logger = logging.getLogger(__name__)


async def capped_list(cursor, length: int, what: str) -> list:
    """Like cursor.to_list(length=...), but logs a warning when the cap is
    actually hit, so silent truncation (events or messages quietly missing
    from a prompt or a listing) is at least visible. Logs only the kind of
    record and the cap -- never ids or content."""
    docs = await cursor.to_list(length=length)
    if len(docs) >= length:
        logger.warning("Result for %s reached its cap of %d -- older/extra records are omitted.", what, length)
    return docs
