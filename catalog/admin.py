from django.contrib import admin
from .models import Category, Product, ProductVariant, Inventory, InventoryTransaction


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'parent', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('name',)


class ProductVariantInline(admin.TabularInline):
    """Allows adding/editing variants directly from the Product form."""
    model = ProductVariant
    extra = 1
    fields = ('sku', 'variant_name', 'regular_price', 'sale_price', 'is_active')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'category',
        'brand',
        'is_published',
        'is_featured',
        'is_deleted',
        'created_at',
    )
    list_filter = ('is_published', 'is_featured', 'is_deleted', 'category')
    search_fields = ('name', 'slug', 'brand')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ProductVariantInline]
    ordering = ('-created_at',)


class InventoryInline(admin.StackedInline):
    """Displays inventory directly inside the Product Variant admin."""
    model = Inventory
    can_delete = False
    readonly_fields = ('available_quantity', 'is_in_stock', 'is_low_stock', 'updated_at')
    fields = (
        'quantity',
        'reserved_quantity',
        'low_stock_threshold',
        'available_quantity',
        ('is_in_stock', 'is_low_stock'),
        'updated_at',
    )


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        'sku',
        'variant_name',
        'product',
        'regular_price',
        'sale_price',
        'effective_price',
        'is_active',
    )
    list_filter = ('is_active', 'product__category')
    search_fields = ('sku', 'variant_name', 'product__name')
    readonly_fields = ('effective_price', 'created_at', 'updated_at')
    inlines = [InventoryInline]
    ordering = ('sku',)


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = (
        'variant',
        'quantity',
        'reserved_quantity',
        'available_quantity',
        'is_in_stock',
        'is_low_stock',
        'updated_at',
    )
    search_fields = ('variant__sku', 'variant__variant_name', 'variant__product__name')
    readonly_fields = ('available_quantity', 'is_in_stock', 'is_low_stock', 'updated_at')


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    """
    STRICTLY READ-ONLY AUDIT LEDGER.
    In fintech and inventory accounting, transactions are an immutable event log.
    No human or admin can edit, delete, or create manual records through the admin panel.
    """
    list_display = (
        'created_at',
        'inventory',
        'transaction_type',
        'quantity',
        'balance_after',
        'reference_id',
    )
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('inventory__variant__sku', 'reference_id', 'description')
    ordering = ('-created_at',)

    # --- Immutability Rules ---
    def has_add_permission(self, request):
        """Prevent manually creating transactions in admin (must be triggered by business logic)."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing past ledger records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent tampering or deleting audit trail entries."""
        return False