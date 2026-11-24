from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from .models import ReturnRequest, Order
from .utils import invalidate_dashboard_cache


@receiver([post_save, post_delete], sender=Order)
def invalidate_order_dashboard_cache(sender, instance, **kwargs):
    invalidate_dashboard_cache()

@receiver([post_delete, post_save], sender=ReturnRequest)
def invalidate_return_dashboard_cache(sender, instance, **kwargs):
    invalidate_dashboard_cache()