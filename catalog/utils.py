from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

STOCK_KEY_PREFIX = "stock:sku:"

def get_redis_stock_key(sku: str) -> str:
    """Returns  the standardized cache key for variant warehouse stock."""
    return f"{STOCK_KEY_PREFIX}{sku}"

def sync_stock_to_redis(sku: str, available_quantity: int):
    """
    Called whenever warehouse inventory changes (initial stock, shipment arrival, manual adjustment).
    Sets the live in-memory stock counter in Redis.
    """
    key = get_redis_stock_key(sku)
    cache.set(key, int(available_quantity), timeout=None)
    logger.info(f"[Redis Stock] Synced SKU '{sku}' with live available stock: {available_quantity}")

def check_and_reserve_redis_stock(sku: str, requested_quantity: int, fallback_available: int) -> bool:
    """
    Atomic check-and-decrement in Redis memory.
    - If stock is sufficient: decrements atomically in RAM in < 0.5ms and returns True.
    - If stock is exhausted: rolls back the decrement in RAM and returns False.
    - If Redis is temporarily down: falls back safely to PostgreSQL.
    """

    key = get_redis_stock_key(sku)

    # Cold start: If key is not in Redis, initialize from DB available stock
    if cache.get(key) is None:
        sync_stock_to_redis(sku, fallback_available)

    try:
        # Atomic decrement in single-threaded Redis memory
        remaining = cache.decr(key, delta=requested_quantity)
        if remaining < 0:
            # Stock ran out! Restore the count and reject
            cache.incr(key, delta=requested_quantity)
            return False
        return True
    except Exception as e:
        logger.error(f"[Redis Stock Error]  Failed atomic decrement for SKU '{sku}': {e}")
        # safe fallback: If redis has a connection issue, fallback to DB check
        return fallback_available >= requested_quantity

def release_redis_stock(sku: str, quantity: int):
    """Releases previously reserved units  back to Redis (e.g. when an item is removed from cart or order is cancelled.)"""
    key = get_redis_stock_key(sku)
    try:
        if cache.get(key) is not None:
            cache.incr(key, delta=quantity)
            logger.info(f"[Redis Stock] Released {quantity} units back for SKU '{sku}'")
    except Exception as e:
        logger.error(f"[Redis Stock Error] Failed to release stock for SKU '{sku}': {e}")