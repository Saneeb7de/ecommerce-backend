from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator


class Cart(models.Model):
    """
    Shopping cart container for an authenticated customer.
    OneToOne relationship ensures each customer has exactly one persistent cart.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'carts'
        verbose_name = 'Shopping Cart'
        verbose_name_plural = 'Shopping Carts'

    def __str__(self):
        return f"Cart of {self.user.email} ({self.total_items} items)"

    @property
    def total_items(self):
        """Sum of all item quantities in the cart."""
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self):
        """Calculates total price across all active line items."""
        return sum(item.line_subtotal for item in self.items.all())

    @property
    def is_empty(self):
        return not self.items.exists()


class CartItem(models.Model):
    """
    Individual line item in the customer's cart.
    Links directly to ProductVariant (the exact purchasable SKU).
    """
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cart_items'
        verbose_name = 'Cart Item'
        verbose_name_plural = 'Cart Items'
        ordering = ['-created_at']
        # Prevent duplicate rows for the same variant in one cart:
        constraints = [models.UniqueConstraint(fields=['cart', 'variant'], name='unique_cart_variant')]

    def __str__(self):
        return f"{self.quantity}x {self.variant.sku} in Cart #{self.cart.id}"

    @property
    def unit_price(self):
        """Uses live effective_price (discounted sale price if active, else regular price)."""
        return self.variant.effective_price

    @property
    def line_subtotal(self):
        """Unit price multiplied by quantity."""
        return Decimal(str(self.unit_price)) * self.quantity

    @property
    def is_in_stock(self):
        """Checks if warehouse has enough physical stock for this requested quantity."""
        if hasattr(self.variant, 'inventory'):
            return self.variant.inventory.available_quantity >= self.quantity
        return False


class Wishlist(models.Model):
    """
    Bookmarked products saved by customer for future purchase.
    Links to master Product (customer bookmarks the product, chooses color/size later).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist_items')
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE, related_name='wishlisted_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wishlists'
        verbose_name = 'Wishlist Item'
        verbose_name_plural = 'Wishlist Items'
        ordering = ['-created_at']
        # Customer cannot bookmark the exact same product multiple times:
        constraints = [models.UniqueConstraint(fields=['user', 'product'], name='unique_user_wishlist_product')]

    def __str__(self):
        return f"{self.user.email} ❤️ {self.product.name}"