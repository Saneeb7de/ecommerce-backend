from django.core.cache import cache

def invalidate_dashboard_cache():
    periods = ['all', 'today', '7d', '30d']
    timelines = ['week', 'month', 'year']

    keys_to_delete = [f"dashboard:metrics:{p}" for p in periods]
    keys_to_delete += [f"dashboard:sales_chart:{t}" for t in timelines]

    cache.delete_many(keys_to_delete)

MAX_PG_BIGINT = 9223372036854775807

def _order_lookup(identifier):
        """Returns a safe ORM lookup dict by primary key (bounded to PG bigint) or order_number."""
        id_str = str(identifier).strip()
        if id_str.isdigit() and int(id_str) <= MAX_PG_BIGINT:
            return {"id": int(id_str)}
        return {"order_number": id_str} 