from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.shortcuts import get_object_or_404
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, F, Min, Case, When, DecimalField

from users.permissions import IsAdmin
from users.pagination import StandardResultsSetPagination

from .models import Category, Product, ProductVariant, InventoryTransaction, Inventory
from .serializers import ProductVariantSerializer, ProductCreateUpdateSerializer, ProductDetailSerializer, ProductListSerializer, ProductVariantWriteSerializer, AdminProductDetailSerializer
from .serializers import InventorySerializer, InventoryTransactionSerializer, CategorySerializer, CategoryTreeSerializer

# SECTION 1: CUSTOMER VIEWS
# public browsing

class CustomerCategoryListView(APIView):
    """
    GET ...catalog/categories/
    Public: Returns the full nested category tree (Root categories with their children).
    Only active categories are included.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        # Fetch only root categories (parent=None) that are active.
        # The recursive CategoryTreeSerializer handles nesting the children automatically.
        root_categories = Category.objects.filter(parent__isnull=True, is_active=True
                                                  ).prefetch_related('children').order_by('name')
        serializer = CategoryTreeSerializer(root_categories, many=True)
        return Response(
            {
                "success": True,
                "count": root_categories.count(),
                "data": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class CustomerProductListView(APIView):
    """
    .../catalog/products/
    Public: List published, non-deleted products with filtering, search, sorting  & pagination.

    Supported Query params:
    - ?search=iphone          (Search in name, brand, description)
    - ?category=electronics   (Filter by category slug or id)
    - ?brand=Apple            (Filter by brand)
    - ?min_price=50000        (Filter by minimum variant price)
    - ?max_price=100000       (Filter by maximum variant price)
    - ?sort=price_asc         (price_asc | price_desc | name_asc | name_desc | newest)
    - ?page=1&page_size=10    (Pagination)
    """

    permission_classes = [AllowAny]

    def get(self, request):
        query_set = Product.objects.filter(is_published=True, is_deleted=False).select_related('category').prefetch_related('variants__inventory')

        # 1. Text Search
        search_query = request.query_params.get('search', '').strip()
        if search_query:
            query_set = query_set.filter(Q(name__icontains=search_query) | Q(brand__icontains=search_query) | Q(description__icontains=search_query))

        # 2. Category Filter (slug or ID)
        category_param = request.query_params.get('category', '').strip()
        if category_param:
            if category_param.isdigit():
                query_set = query_set.filter(Q(category_id=category_param) | Q(category__parent_id=category_param))
            else:
                query_set = query_set.filter(Q(category__slug=category_param) | Q(category__parent__slug=category_param))

        # 3. Brand Filter
        brand_param = request.query_params.get('brand', '').strip()
        if brand_param:
            query_set = query_set.filter(brand__iexact=brand_param)

        # ── 4. Dynamic Price Calculation in SQL (Postgres optimization) ───
        # We calculate each active variant's effective price directly in SQL:
        # if sale_price > 0 then sale_price, else regular_price.
        effective_price_sql = Case(
            When(variants__sale_price__isnull=False, variants__sale_price__gt=0, then=F('variants__sale_price'),),
            default=F('variants__regular_price'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )

        query_set = query_set.annotate(
            calculated_min_price=Min(effective_price_sql, filter=Q(variants__is_active=True))
        )

        # 5. Price Range Filtering
        min_price = request.query_params.get('min_price')
        if min_price:
            try:
                query_set = query_set.filter(calculated_min_price__gte=Decimal(min_price))
            except Exception:
                pass

        max_price = request.query_params.get('max_price')
        if max_price:
            try:
                query_set = query_set.filter(calculated_min_price__lte=Decimal(max_price))
            except Exception:
                pass

        # 6. Sorting
        sort_param = request.query_params.get('sort', 'newest')
        sort_mapping = {
            'price_asc': 'calculated_min_price',
            'price_desc': '-calculated_min_price',
            'name_asc': 'name',
            'name_desc': '-name',
            'newest': '-created_at',
        }
        order_field = sort_mapping.get(sort_param, '-created_at')
        query_set = query_set.order_by(order_field)

        # 7. Pagination
        paginator = StandardResultsSetPagination()
        paginated_products = paginator.paginate_queryset(query_set, request)
        serializer = ProductListSerializer(paginated_products, many=True)
        return paginator.get_paginated_response(serializer.data)

class CustomerProductDetailView(APIView):
    """
    GET ..../catalog/products/<slug>/
    Public: Retrieve full product details including variants and stock status by slug.
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        product = get_object_or_404(
            Product.objects.select_related('category').prefetch_related('variants__inventory'),
            slug=slug,
            is_published=True,
            is_deleted=False,
        )
        serializer = ProductDetailSerializer(product)
        return Response(
            {
                "success": True,
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )

class CustomerFeaturedProductsView(APIView):
    """
    GET ..../catalog/products/featured/
    Public: Retrieve featured products for homepage / hero banners.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        featured_product = Product.objects.filter(
            is_featured=True, is_published=True, is_deleted=False
        ).select_related('category').prefetch_related('variants__inventory').order_by('-created_at')[:8]

        serializer = ProductListSerializer(featured_product, many=True)
        return Response(
            {
            "success": True,
            "count": len(serializer.data),
            "data": serializer.data
            },
        status=status.HTTP_200_OK
        )




# SECTION 2: ADMIN CATEGORY MANAGEMENT
class AdminCategoryListCreateView(APIView):
    """
    GET  /api/v1/admin/categories/ -> Paginated flat list of categories with search
    POST /api/v1/admin/categories/ -> Create a new category
    """

    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = Category.objects.select_related('parent').order_by('name')
        search_query = request.query_params.get('search', '').strip()
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(slug__icontains=search_query))

        paginator = StandardResultsSetPagination()
        paginated_categories = paginator.paginate_queryset(queryset, request)
        serializer = CategorySerializer(paginated_categories, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = CategorySerializer(data=request.data)
        if serializer.is_valid():
            category = serializer.save()
            return Response(
                {
                    "success": True,
                    "message": "Category created successfully.",
                    "data": CategorySerializer(category).data
                },
                status=status.HTTP_201_CREATED
            )
        return Response(
            {
                "success": False,
                "errors": serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )


class AdminCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/admin/categories/<id>/ -> Retrieve category
    PUT    /api/v1/admin/categories/<id>/ -> Update full category
    PATCH  /api/v1/admin/categories/<id>/ -> Update partial category
    DELETE /api/v1/admin/categories/<id>/ -> Soft-delete category (is_active = False)
    """

    queryset = Category.objects.select_related('parent')
    serializer_class = CategorySerializer
    permission_classes = [IsAdmin]

    # ⚠️ CRITICAL: You MUST override perform_destroy!
    def perform_destroy(self, instance):
        """Soft-delete instead of permanent database deletion."""
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])


class AdminProductListCreateView(APIView):
    """
    GET  /api/v1/admin/products/ -> List all products (includes soft-deleted & unpublished)
    POST /api/v1/admin/products/ -> Create a new master product (enforces minimum 3 photos)
    """

    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = Product.objects.select_related('category').prefetch_related('variants__inventory').order_by('-created_at')
        search_query = request.query_params.get('search', '').strip()
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(brand__icontains=search_query))

        is_deleted = request.query_params.get('is_deleted')
        if is_deleted is not None:
            queryset = queryset.filter(is_deleted=is_deleted.lower() == 'true')

        is_published = request.query_params.get('is_published')
        if is_published is not None:
            queryset = queryset.filter(is_published=is_published.lower() == 'true')

        paginator = StandardResultsSetPagination()
        paginated_products = paginator.paginate_queryset(queryset, request)
        serializer = AdminProductDetailSerializer(paginated_products, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = ProductCreateUpdateSerializer(data=request.data)
        if serializer.is_valid():
            product = serializer.save()
            return Response(
                {
                    "success": True,
                    "message": "Product created successfully.",
                    "data": AdminProductDetailSerializer(product).data,
                },
                status=status.HTTP_201_CREATED
            )
        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

class AdminProductDetailView(APIView):
    """
    GET    /api/v1/admin/products/<id>/ -> Retrieve product details
    PUT    /api/v1/admin/products/<id>/ -> Update product (supports partial updates)
    PATCH  /api/v1/admin/products/<id>/ -> Delegates to PUT
    DELETE /api/v1/admin/products/<id>/ -> Soft-delete product (is_deleted = True)
    """
    permission_classes = [IsAdmin]

    def get_object(self, pk):
        return get_object_or_404(
            Product.objects.select_related('category').prefetch_related('variants__inventory'),
            pk=pk,
        )

    def get(self, request, pk):
        product = self.get_object(pk)
        return Response({"success": True, "data": AdminProductDetailSerializer(product).data})

    def put(self, request, pk):
        product = self.get_object(pk)
        serializer = ProductCreateUpdateSerializer(product, data=request.data, partial=True)
        if serializer.is_valid():
            updated_product = serializer.save()
            return Response(
                {
                    "success": True,
                    "message": "Product updated successfully.",
                    "data": AdminProductDetailSerializer(updated_product).data
                }
            )
        return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, pk):
        return self.put(request, pk)
    
    def delete(self, request, pk):
        product = self.get_object(pk)
        product.is_deleted = True
        product.save(update_fields=['is_deleted', 'updated_at'])
        return Response(
            {"success": True, "message": f"Product '{product.name}' soft-deleted successfully."},
            status=status.HTTP_200_OK,
        )


class AdminProductVariantListCreateView(APIView):
    """
    POST /api/v1/admin/products/<product_id>/variants/
    Creates a new variant for a product AND automatically initializes its Inventory record.
    """
    permission_classes = [IsAdmin]

    def get(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id, is_deleted=False)
        variants = product.variants.all()
        serializer = ProductVariantSerializer(variants, many=True)
        return Response({"success": True, "count": variants.count(), "data": serializer.data})

    @transaction.atomic
    def post(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id, is_deleted=False)
        serializer = ProductVariantWriteSerializer(data=request.data)
        if serializer.is_valid():
            # Save the new variant attached to the parent product
            variant = serializer.save(product=product)

            # Auto-initialize warehouse inventory with 0 stock
            initial_stock = int(request.data.get('initial_stock', 0))
            inventory = Inventory.objects.create(
                variant=variant,
                quantity=initial_stock,
                reserved_quantity=0,
            )

            # If initial stock was provided, create an initial ledger entry
            if initial_stock > 0:
                InventoryTransaction.objects.create(
                    inventory=inventory,
                    transaction_type=InventoryTransaction.TransactionType.PURCHASE_RECEIPT,
                    quantity=initial_stock,
                    balance_after=initial_stock,
                    reference_id="INITIAL_STOCK",
                    description="Initial stock received on variant creation.",
                )

            return Response(
                 {
                    "success": True,
                    "message": f"Variant '{variant.sku}' created with initial stock of {initial_stock}.",
                    "data": ProductVariantSerializer(variant).data,
                },
                status=status.HTTP_201_CREATED
            )
        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )



class AdminProductVariantDetailView(APIView):
    """
    GET    /api/v1/admin/variants/<id>/ -> Retrieve variant details
    PUT    /api/v1/admin/variants/<id>/ -> Update variant (supports partial updates)
    PATCH  /api/v1/admin/variants/<id>/ -> Delegates to PUT
    DELETE /api/v1/admin/variants/<id>/ -> Deactivate variant (is_active = False)
    """

    permission_classes = [IsAdmin]

    def get_object(self, pk):
        return get_object_or_404(ProductVariant.objects.select_related('product', 'inventory'), pk=pk)

    def get(self, request, pk):
        variant = self.get_object(pk)
        return Response({"success": True, "data": ProductVariantSerializer(variant).data})

    def put(self, request, pk):
        variant = self.get_object(pk)
        serializer = ProductVariantWriteSerializer(variant, data=request.data, partial=True)
        if serializer.is_valid():
            updated_variant = serializer.save()
            return Response(
                {
                    "success": True,
                    "message": "Variant updated.",
                    "data": ProductVariantSerializer(updated_variant).data,
                }
            )
        return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        variant = self.get_object(pk)
        variant.is_active = False
        variant.save(update_fields=['is_active', 'updated_at'])
        return Response({"success": True, "message": f"Variant '{variant.sku}' deactivated."},
                        status=status.HTTP_200_OK)


class AdminInventoryStockAdjustView(APIView):
    """
    POST /api/v1/admin/inventory/adjust/
    Fintech/Double-Entry Ledger Pattern:
    Adjust warehouse stock with pessimistic locking (select_for_update)
    and an immutable InventoryTransaction record.
    Payload:
    {
        "sku": "IPHONE15-BLK-128",
        "adjustment_quantity": 50,  // Positive (+50 for arrival) or Negative (-5 for damage)
        "transaction_type": "PURCHASE_RECEIPT", // or MANUAL_ADJUSTMENT, RETURN_RESTOCK
        "reference_id": "PO-99214",
        "description": "Supplier shipment received"
    }
    """

    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request):
        sku = request.data.get('sku')
        adjustment_quantity = request.data.get('adjustment_quantity')
        transaction_type = request.data.get('transaction_type', InventoryTransaction.TransactionType.MANUAL_ADJUSTMENT)
        reference_id = request.data.get('reference_id', '')
        description = request.data.get('description', '')

        # Basic validations
        if not sku or adjustment_quantity is None:
            return Response(
                {"success": False, "message": "'sku' and 'adjustment_quantity' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            adjustment_quantity = int(adjustment_quantity)
        except ValueError:
            return Response(
                {"success": False, "message": "'adjustment_quantity' must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Pessimistic Row Lock (select_for_update) ─────────────────
        # Locks this specific inventory row in Postgres until transaction finishes
        try:
            inventory = Inventory.objects.select_for_update().select_related('variant').get(variant__sku=sku)
        except Inventory.DoesNotExist:
            return Response(
                {"success": False, "message": f"Inventory for SKU '{sku}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        new_total_quantity = inventory.quantity + adjustment_quantity
        # Prevent warehouse stock from dropping below zero or reserved units
        if new_total_quantity < 0:
            return Response(
                {
                    "success": False,
                    "message": f"Cannot deduct {abs(adjustment_quantity)} units. Current stock is {inventory.quantity}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_total_quantity < inventory.reserved_quantity:
            return Response(
                {
                    "success": False,
                    "message": f"Cannot reduce stock below reserved orders ({inventory.reserved_quantity} units reserved).",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1. Update live stock snapshot
        inventory.quantity = new_total_quantity
        inventory.save(update_fields=['quantity', 'updated_at'])

        # 2. Append immutable ledger transaction entry
        txn = InventoryTransaction.objects.create(
            inventory=inventory,
            transaction_type=transaction_type,
            quantity=adjustment_quantity,
            balance_after=new_total_quantity,
            reference_id=reference_id,
            description=description,
        )

        return Response(
            {
                "success": True,
                "message": f"Stock for SKU '{sku}' adjusted by {adjustment_quantity:+d}. New total: {new_total_quantity}.",
                "data": {
                    "inventory": InventorySerializer(inventory).data,
                    "transaction": InventoryTransactionSerializer(txn).data,
                },
            },
            status=status.HTTP_200_OK,
        )


class AdminInventoryTransactionListView(APIView):
    """
    GET /api/v1/admin/inventory/transactions/
    Audit Trail: Lists historical ledger transactions with filters & pagination.
    Supported Query Params:
    - ?sku=IPHONE15-BLK-128
    - ?type=PURCHASE_RECEIPT
    - ?page=1
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = InventoryTransaction.objects.select_related('inventory__variant').order_by('-created_at')

        sku = request.query_params.get('sku', '').strip()
        if sku:
            queryset = queryset.filter(inventory__variant__sku__iexact=sku)

        txn_type = request.query_params.get('type', '').strip()
        if txn_type:
            queryset = queryset.filter(transaction_type=txn_type)

        paginator = StandardResultsSetPagination()
        paginated_txns = paginator.paginate_queryset(queryset, request)
        serializer = InventoryTransactionSerializer(paginated_txns, many=True)
        return paginator.get_paginated_response(serializer.data)