from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.core.cache import cache
from .models import Category, Product, ProductVariant, Inventory
from .utils import sync_stock_to_redis

@receiver([post_save, post_delete], sender=Category)
def invalidate_category_cache(sender, instance, **kwargs):
    cache.delete("catalog:category_tree")

@receiver([post_save, post_delete], sender=Product)
def invalidate_product_cache(sender, instance, **kwargs):
    cache.delete(f"catalog:product:{instance.slug}")
    cache.delete("catalog:featured_product")
    cache.delete("catalog:category_tree")

@receiver([post_save, post_delete], sender=ProductVariant)
def invalidate_variant_cache(sender, instance, **kwargs):
    if instance.product:
        cache.delete(f"catalog:product:{instance.product.slug}")
        cache.delete("catalog:featured_product")

@receiver(post_save, sender=Inventory)
def invalidate_inventory_cache(sender, instance, **kwargs):
    if instance.variant:
        sync_stock_to_redis(instance.variant.sku, instance.available_quantity)
        if instance.variant.product:
            cache.delete(f"catalog:product:{instance.variant.product.slug}")