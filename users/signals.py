from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User
from orders.models import Wallet
from cart.models import Cart

@receiver(post_save, sender=User)
def create_customer_wallet_and_cart(sender, instance, created, **kwargs):
    if created and instance.role == User.Role.CUSTOMER:
        Wallet.objects.get_or_create(user=instance)
        Cart.objects.get_or_create(user=instance)