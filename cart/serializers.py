from decimal import Decimal
from rest_framework import serializers

from .models import Cart, CartItem, Wishlist
from catalog.models import Product, ProductVariant


class CartItemSerializer(serializers.ModelSerializer):
    """
    Renders full details of an item in the cart:
    - Live effective price (reflects active sale discounts automatically)
    - Line subtotal (unit_price * quantity)
    - Live warehouse stock check (alerts frontend if an item went out of stock)
    """

    variant_id = serializers.IntegerField(source='variant.id', read_only=True)
    sku = serializers.CharField(source='variant.sku', read_only=True)
    variant_name = serializers.CharField(source='variant.variant_name', read_only=True)
    attributes = serializers.JSONField(source='variant.attributes', read_only=True)
    product_id = serializers.IntegerField(source='variant.product.id', read_only=True)
    product_name = serializers.CharField(source='variant.product.name', read_only=True)
    product_slug = serializers.CharField(source='variant.product.slug', read_only=True)
    primary_image = serializers.URLField(source='variant.product.primary_image', read_only=True)
    unit_price = serializers.SerializerMethodField()
    line_subtotal = serializers.SerializerMethodField()
    is_in_stock = serializers.SerializerMethodField()
    available_stock = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            'id', 'variant_id', 'sku', 'variant_name', 'attributes',
            'product_id', 'product_name', 'product_slug', 'primary_image',
            'unit_price', 'quantity', 'line_subtotal', 'is_in_stock', 'available_stock'
        ]
        read_only_fields = ['id', 'unit_price', 'line_subtotal', 'is_in_stock', 'available_stock']

    def get_unit_price(self, obj):
        return str(obj.unit_price)

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal)

    def get_is_in_stock(self, obj):
        return obj.is_in_stock

    def get_available_stock(self, obj):
        if hasattr(obj.variant, 'inventory'):
            return obj.variant.inventory.available_quantity
        return 0

class CartSerializer(serializers.ModelSerializer):
    """
    Full cart representation:
    - List of nested items
    - Total items count
    - Computed subtotal across all items
    """
    items = CartItemSerializer(many=True, read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    subtotal = serializers.SerializerMethodField()
    is_empty = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cart
        fields = ['id', 'total_items', 'subtotal', 'is_empty', 'items', 'updated_at']
        read_only_fields = ['id', 'total_items', 'subtotal', 'is_empty', 'updated_at']

    def get_subtotal(self, obj):
        return str(obj.subtotal)


class AddToCartSerializer(serializers.Serializer):
    """
    Validates adding an item to the shopping bag.
    Enforces live warehouse stock check before allowing the item to be added.
    """

    variant_id = serializers.IntegerField(required=False)
    quantity = serializers.IntegerField(default=1, min_value=1)

    def validate(self, data):
        variant_id = data.get('variant_id')
        if not variant_id:
            raise serializers.ValidationError({"variant_id": "variant_id is required"})

        # 1. Fetch variant with related product and warehouse inventory
        try:
            variant = ProductVariant.objects.select_related('product', 'inventory').get(pk=variant_id)
        except ProductVariant.DoesNotExist:
            raise serializers.ValidationError({"variant_id": f"Product variant with id '{variant_id}' does not exist."})

        # 2. Check if product is active and not soft-deleted
        if not variant.is_active or variant.product.is_deleted or not variant.product.is_published:
            raise serializers.ValidationError({"variant_id": "This product variant is currently unavailable."})

        # 3. Check warehouse stock availability
        requested_qty = data.get('quantity', 1)
        available_qty = variant.inventory.available_quantity if hasattr(variant, 'inventory') else 0

        if available_qty < 1:
            raise serializers.ValidationError({"quantity": "This item is currently out of stock."})

        if requested_qty > available_qty:
            raise serializers.ValidationError(
                {"quantity": f"Cannot add {requested_qty} items. Only {available_qty} units available in stock."})

        data['variant'] = variant
        return data


class UpdateCartItemQuantitySerializer(serializers.Serializer):
    """
    Validates updating line item quantity (+ / -).
    """

    quantity = serializers.IntegerField(min_value=1)

    def validate_quantity(self, value):
        if value < 1:
            raise serializers.ValidationError("Quantity must be at least 1.")
        return value


class WishlistItemSerializer(serializers.ModelSerializer):
    """
    Renders customer's saved bookmark:
    Includes product image, title, slug, brand, category, and live stock indicator.
    """

    product_id = serializers.IntegerField(source='product.id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    brand = serializers.CharField(source='product.brand', read_only=True)
    category_name = serializers.CharField(source='product.category.name', read_only=True)
    primary_image = serializers.URLField(source='product.primary_image', read_only=True)

    is_in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Wishlist
        fields = [
            'id', 'product_id', 'product_name', 'product_slug', 'brand',
            'category_name', 'primary_image', 'is_in_stock', 'created_at'
        ]

        read_only_fields = ['id', 'created_at']

    def get_is_in_stock(self, obj):
        return obj.product.variants.filter(is_active=True, inventory__quantity__gt=0).exists()


class WishlistToggleSerializer(serializers.Serializer):
    """
    Validates bookmark toggle request.
    """

    product_id = serializers.IntegerField(required=False)

    def validate(self, data):
        product_id = data.get('product_id')
        if not product_id:
            raise serializers.ValidationError({"product_id": "'product_id' is required"})

        try:
            product = Product.objects.get(pk=product_id, is_deleted=False)
        except Product.DoesNotExist:
            raise serializers.ValidationError({"product_id": f"Product with id '{product_id}' does not exist."})

        data['product'] = product
        return data