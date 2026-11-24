from django.core.cache import cache

def invalidate_dashboard_cache():
    periods = ['all', 'today', '7d', '30d']
    timelines = ['week', 'month', 'year']

    keys_to_delete = [f"dashboard:metrics:{p}" for p in periods]
    keys_to_delete += [f"dashboard:sales_chart:{t}" for t in timelines]

    cache.delete_many(keys_to_delete)