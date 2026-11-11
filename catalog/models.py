from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from decimal import Decimal


class Category(models.Model):
    """
    Self-referencing Category Tree supporting infinite subcategory nesting.
    Includes soft-delete (is_active) to preserve product associations.
    """

    name = models.CharField(max_length=100, db_index=True)
    slug = models.SlugField(max_length=120, unique=True, db_index=True)
    description = models.TextField(blank=True, null=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    image_url = models.URLField(max_length=500, blank=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'categories'
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        full_path = [self.name]
        k = self.parent
        while k is not None:
            full_path.append(k.name)
            k = k.parent
        return ' → ' .join(full_path[::-1])

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(models.Model):
    """
    Master Product entity.
    Includes primary_image (required) + gallery_images (JSONField array of URLs).
    Includes soft-delete (is_deleted) and publication visibility controls.
    """
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, db_index=True)

    brand = models.CharField(max_length=100, db_index=True, blank=True, null=True)
    short_description = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField()

    primary_image = models.URLField(max_length=500)
    gallery_images = models.JSONField(default=list, blank=True)

    is_featured = models.BooleanField(default=False, db_index=True)
    is_published = models.BooleanField(default=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'products'
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['name'], condition=Q(is_deleted=False), name='unique_active_product_name')]

    def __str__(self):
        return f"{self.name} ({self.brand or 'Generic'})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ProductVariant(models.Model):
    """
    Purchasable SKU (Stock Keeping Unit).
    Defines distinct attributes (Color, Size, Storage) and price points.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    sku = models.CharField(max_length=100, unique=True, db_index=True)
    variant_name = models.CharField(max_length=150) # e.g., 'Matte Black / 256GB'
    attributes = models.JSONField(default=dict, blank=True) # e.g., {"color": "Black", "storage": "256GB"}
    regular_price = models.DecimalField(max_digits=12, decimal_places=2)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product_variants'
        verbose_name = 'Product Variant'
        verbose_name_plural = 'Product Variants'
        ordering = ['id']

    def __str__(self):
        return f"{self.product.name} - {self.variant_name} [{self.sku}]"

    def clean(self):
        if self.sale_price is not None and self.sale_price > self.regular_price:
            raise ValidationError({"sale_price": "Sale price cannot be greater than regular price."})

    @property
    def effective_price(self):
        """Returns the discounted price if valid, otherwise regular price"""
        if self.sale_price is not None and self.sale_price > Decimal("0.00"):
            return self.sale_price
        return self.regular_price

class Inventory(models.Model):
    """
    Live warehouse inventory snapshot per ProductVariant.
    Maintains available vs reserved stock to prevent checkout overselling.
    """

    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name='inventory')
    quantity = models.PositiveIntegerField(default=0)  # Physical warehouse stock on hand
    reserved_quantity = models.PositiveIntegerField(default=0) # Locked in active checkouts
    low_stock_threshold = models.PositiveIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inventories'
        verbose_name = 'Inventory'
        verbose_name_plural = 'Inventories'

    def __str__(self):
        return f"Stock for {self.variant.sku}: Total={self.quantity}, Available={self.available_quantity}"

    @property
    def available_quantity(self):
        """Units immediatly available for customers to add to cart or buy."""
        return max(0, self.quantity - self.reserved_quantity)

    @property
    def is_in_stock(self):
        return self.available_quantity > 0

    @property
    def is_low_stock(self):
        return 0 < self.available_quantity <= self.low_stock_threshold

class InventoryTransaction(models.Model):
    """
    Double-entry immutable stock ledger.
    Every units added, reserved, or shipped must have a transaction record.
    """

    class TransactionType(models.TextChoices):
        PURCHASE_RECEIPT = 'PURCHASE_RECEIPT', 'Stock Received from Supplier'
        ORDER_RESERVED = 'ORDER_RESERVED', 'Stock Reserved for Pending Order'
        ORDER_FULFILLED = 'ORDER_FULFILLED', 'Stock Deducted (Order Shipped)'
        ORDER_CANCELLED = 'ORDER_CANCELLED', 'Reservation Released (Order Cancelled)'
        MANUAL_ADJUSTMENT = 'MANUAL_ADJUSTMENT', 'Manual Adjustment by Admin'
        RETURN_RESTOCK = 'RETURN_RESTOCK', 'Restocked from Customer Return'

    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=30, choices=TransactionType.choices, db_index=True)
    quantity = models.IntegerField() # Positive for additions, negative for deductions
    balance_after = models.PositiveIntegerField() # Snapshot of total quantity after change
    reference_id = models.CharField(max_length=100, blank=True, null=True, db_index=True) # Order #, PO #
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'inventory_transactions'
        verbose_name = 'Inventory Transaction'
        verbose_name_plural = 'Inventory Transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d')}] {self.transaction_type} ({self.quantity:+d}) for {self.inventory.variant.sku}"