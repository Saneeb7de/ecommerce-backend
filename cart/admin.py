from django.contrib import admin
from .models import Cart, CartItem, Wishlist


class CartItemInline(admin.TabularInline):
    """
    Shows cart items in a table directly inside the Cart page.
    """
    model = CartItem
    extra = 0
    fields = ['variant', 'quantity', 'unit_price', 'line_subtotal', 'is_in_stock']
    readonly_fields = ['unit_price', 'line_subtotal', 'is_in_stock']


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """
    Admin configuration for customer shopping carts.
    """
    list_display = ['id', 'user_email', 'get_total_items', 'get_subtotal', 'updated_at']
    search_fields = ['user__email', 'user__name']
    readonly_fields = ['get_total_items', 'get_subtotal', 'created_at', 'updated_at']
    inlines = [CartItemInline]

    @admin.display(description='Customer Email')
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description='Total Items')
    def get_total_items(self, obj):
        return obj.total_items

    @admin.display(description='Cart Subtotal ($)')
    def get_subtotal(self, obj):
        return f"${obj.subtotal:.2f}"


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    """
    Admin configuration for customer wishlists.
    """
    list_display = ['id', 'user_email', 'product_name', 'created_at']
    search_fields = ['user__email', 'product__name']
    list_filter = ['created_at']

    @admin.display(description='Customer Email')
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description='Bookmarked Product')
    def product_name(self, obj):
        return obj.product.name