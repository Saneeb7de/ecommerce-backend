from rest_framework import serializers
from django.utils.text import slugify
from decimal import Decimal

from .models import Category, Product, ProductVariant, Inventory, InventoryTransaction

class CategorySerializer(serializers.ModelSerializer):
    """
    Flat category representaion.
    Used in: Product Detail, Admin category list.
    Shows parents name as a readable string instead of raw FK id. 
    """

    parent_name = serializers.CharField(source='parent.name', read_only=True, default=None)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'parent', 'parent_name', 'image_url', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']

    def validate_name(self, value):
        slug = slugify(value)
        if not slug:
            raise serializers.ValidationError("Category name must contain letters or numbers.")
        qs = Category.objects.filter(slug=slug)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(f"A category with the name '{value}' already exists.")
        return value
    def validate_parent(self, value):
        if self.instance and value and value.pk == self.instance.pk:
            raise serializers.ValidationError("A category cannot be its own parent.")
        return value

class CategoryTreeSerializer(serializers.ModelSerializer):
    """
    Recursive category tree.
    Used in: Public category listing - shows nested children.

    How recursion works here:
    - 'children' is the related_name on te Category.parent ForiegnKey.
    - We references  'self' (CategoryTreeSerializer itself) for nested children.
    - many=True because one parent can have muny children.
    - read_only=True because this is only for output, not input.
    """

    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'image_url', 'is_active', 'children']

    def get_children(self, obj):
        # Only return active children to public customers
        active_children = obj.children.filter(is_active=True)
        return CategoryTreeSerializer(active_children, many=True).data



# Inventory serializers

class InventorySerializer(serializers.ModelSerializer):
    """
    Read-only stocks snapshot exposed inside variant detail.
    Computed @property fields must be declared as SerializerMethodField
    because they are not actual database columns.
    """

    available_quantity = serializers.SerializerMethodField()
    is_in_stock = serializers.SerializerMethodField()
    is_low_stock = serializers.SerializerMethodField()

    class Meta:
        model = Inventory
        fields = ['quantity', 'reserved_quantity', 'low_stock_threshold', 'available_quantity', 'is_in_stock', 'is_low_stock', 'updated_at']
        read_only_fields = ['quantity', 'reserved_quantity', 'low_stock_threshold', 'updated_at']

    def get_available_quantity(self, obj):
        return obj.available_quantity

    def get_is_in_stock(self, obj):
        return obj.is_in_stock

    def get_is_low_stock(self, obj):
        return obj.is_low_stock

class InventoryTransactionSerializer(serializers.ModelSerializer):
    """
    Read-only immutable ledger entry.
    Admins can VIEW but never create/edit/delete via API.
    'inventory' is shown as the variant SKU for readability
    """
    variant_sku = serializers.CharField(source='inventory.variant.sku', read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = ['id', 'variant_sku', 'transaction_type', 'quantity', 'balance_after', 'reference_id', 'description', 'created_at']
        read_only_fields = ['id', 'transaction_type', 'quantity', 'balance_after', 'reference_id', 'description', 'created_at']


# product variant serializers
class ProductVariantSerializer(serializers.ModelSerializer):
    """
    Full variant detail including live inventory.
    Used in: Product detail page (customer + admin).
    effectivr_price is a @property - exposed via SerializerMethodField.
    """

    effective_price = serializers.SerializerMethodField()
    inventory = InventorySerializer(read_only=True)

    class Meta:
        model = ProductVariant
        fields = ['id', 'sku', 'variant_name', 'attributes', 'regular_price', 'sale_price', 'effective_price', 'is_active', 'inventory', 'created_at', 'updated_at']
        read_only_fields = ['id', 'effective_price', 'created_at', 'updated_at']

    def get_effective_price(self, obj):
        return str(obj.effective_price)  # Decimal → string for safe JSON


class ProductVariantWriteSerializer(serializers.ModelSerializer):
    """
    Used by Admin when CREATING or UPDATING a variant.
    Applies the clean() validation from the model (sale_price <= regular_price).
    Does NOT include inventory (inventory is managed seperately). 
    """

    class Meta:
        model = ProductVariant
        fields = ['sku', 'variant_name', 'attributes', 'regular_price', 'sale_price', 'is_active']

    def validate(self, data):
        """
        Run model-level clean() to enforce sale_price <= regular_price.
        We call it manually here because DRF doesn't auto-call model.clean()
        """

        sale_price = data.get('sale_price')
        regular_price = data.get('regular_price')

        # On partial update (PATCH), if a price wasn't sent, use the existing database value
        if self.instance:
            if 'sale_price' not in data:
                sale_price = self.instance.sale_price
            if 'regular_price' not in data:
                regular_price = self.instance.regular_price
        if regular_price is not None and regular_price <= Decimal('0.00'):
            raise serializers.ValidationError({"regular_price": "Regular price must be greater than zero."})
        if sale_price is not None and sale_price < Decimal('0.00'):
            raise serializers.ValidationError({"sale_price": "Sale price cannot be negative."})
        
        if sale_price is not None and regular_price is not None and sale_price > regular_price:
            raise serializers.ValidationError({"sale_price": "Sale price cannot be greater than regular price."})
        return data
    



# product serializer
class ProductListSerializer(serializers.ModelSerializer):
    """
    COMPACT product card - used for listing pages (customer-facing).
    Shows only what a product card on a shop page needs:
    name, slug, brand, image, cheapest effective price, in-stock status.
    
    Why cheapest price? A product like "iphone 18 pro" shows the range
    "Starting from  ₹179,900" - we find the cheapestactive variant.
    """

    category_name = serializers.CharField(source='category.name', read_only=True)
    starting_price = serializers.SerializerMethodField()
    is_in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'name', 'slug', 'brand', 'category_name', 'primary_image', 'is_featured', 'starting_price', 'is_in_stock']

    def get_starting_price(self, obj):
        # 🚀 1. Instant read from Postgres SQL annotation (0 extra queries):
        if hasattr(obj, 'calculated_min_price') and obj.calculated_min_price is not None:
            return str(obj.calculated_min_price)
        # 2. In-memory evaluation using prefetched variants (0 extra queries):
        active_variants = [v for v in obj.variants.all() if v.is_active]
        if not active_variants:
            return None
        return str(min(v.effective_price for v in active_variants))
    def get_is_in_stock(self, obj):
        return any(
            v.is_active and hasattr(v, 'inventory') and v.inventory.available_quantity > 0
            for v in obj.variants.all()
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    FULL product detail - used for the product detail page (customer-facing).
    Includes all variants with their inventory snapshots.
    """
    category = CategorySerializer(read_only=True)  # <-- Keeps it lightweight & fast
    variants = serializers.SerializerMethodField()
    class Meta:
        model = Product
        exclude = ['is_deleted']

    def get_variants(self, obj):
        active_variants = [v for v in obj.variants.all() if v.is_active]
        return ProductVariantSerializer(active_variants, many=True).data


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Admin: CRAETE or UPDATE a product.

    Photo validaion rule (enforced at API layer, not db layer):
    - primary_image is always required (enforced by the model - nonull=True).
    - gallery_image must have at least2 items.
    - Together: 1 primary + 2 gallery = minimum 3 photos total.

    Why serializer and not model? Because if we enforced this in the DB
    constraint, there would be a chicken-and-egg problem:
    we can't save the product to get a PK before we save photos,
    but photos need the product PK as a FK. with URLS in JSONField,
    we can validate count at serializer time, before save
    """

    class Meta:
        model = Product
        fields = ['name', 'category', 'brand', 'short_description', 'description', 'primary_image', 'gallery_images', 'is_featured', 'is_published']

    def validate_gallery_images(self, value):
        """
        Individual field validator for gallery_images.
        DRF calls validate_<field_name>() automatically.
        """
        if not isinstance(value, list):
            raise serializers.ValidationError("gallery images must be a JSON array of URL strings.")
        if len(value) < 2:
            raise serializers.ValidationError("A product must have at least 2 gallery images. (plus 1 primary_image = minimum 3 photos total).")
        for url in value:
            if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                raise serializers.ValidationError(f"Each gallery image must be a valid URL string. Got: {url!r}")
        return value

    def validate(self, data):
        name = data.get('name')
        if name:
            slug = slugify(name)
            if not slug:
                raise serializers.ValidationError({"name": "Product name must contain letters or numbers."})
            name_qs = Product.objects.filter(name=name, is_deleted=False)
            slug_qs = Product.objects.filter(slug=slug)
            if self.instance:
                name_qs = name_qs.exclude(pk=self.instance.pk)
                slug_qs = slug_qs.exclude(pk=self.instance.pk)
            if name_qs.exists() or slug_qs.exists():
                raise serializers.ValidationError({"name": f"A product with the name '{name}' already exists."})
        # Enforce 3-photo rule even when gallery_images is omitted on POST
        if not self.instance:
            gallery = data.get('gallery_images')
            if not gallery or len(gallery) < 2:
                raise serializers.ValidationError({
                    "gallery_images": "A product must have at least 2 gallery images (plus 1 primary_image = minimum 3 photos total)."
                })
        # Prevent attaching a product to a soft-deleted/inactive category
        category = data.get('category')
        if category and not category.is_active:
            raise serializers.ValidationError({"category": "Cannot assign a product to an inactive category."})
        return data

class AdminProductDetailSerializer(serializers.ModelSerializer):
    """
    Admin-only full product view — includes is_deleted, all variants, all fields.
    Used for GET .../admin/products/<id>.
    """
    category = CategorySerializer(read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = '__all__'