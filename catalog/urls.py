from django.urls import path
from .views import (
    CustomerCategoryListView,
    CustomerProductListView,
    CustomerFeaturedProductsView,
    CustomerProductDetailView,
    AdminCategoryListCreateView,
    AdminCategoryDetailView,
    AdminProductListCreateView,
    AdminProductDetailView,
    AdminProductVariantListCreateView,
    AdminProductVariantDetailView,
    AdminInventoryStockAdjustView,
    AdminInventoryTransactionListView,
)

urlpatterns = [
    # ─────────────────────────────────────────────────────────────
    # SECTION 1: CUSTOMER (PUBLIC) ENDPOINTS
    # ─────────────────────────────────────────────────────────────
    path('catalog/categories/', CustomerCategoryListView.as_view(), name='customer-category-list'),
    path('catalog/products/', CustomerProductListView.as_view(), name='customer-product-list'),

    # ⚠️ CRITICAL URL ORDER: 'featured/' MUST be placed BEFORE '<slug:slug>/'!
    # Django evaluates URLs top-to-bottom. If '<slug:slug>/' were placed first,
    # visiting 'products/featured/' would make Django treat the word 'featured' 
    # as a product slug and throw a 404!
    path('catalog/products/featured/', CustomerFeaturedProductsView.as_view(), name='customer-featured-products'),
    path('catalog/products/<slug:slug>/', CustomerProductDetailView.as_view(), name='customer-product-detail'),

    # ─────────────────────────────────────────────────────────────
    # SECTION 2: ADMIN (PROTECTED) ENDPOINTS
    # ─────────────────────────────────────────────────────────────

    # ── Category Management ──
    path('admin/categories/', AdminCategoryListCreateView.as_view(), name='admin-category-list-create'),
    path('admin/categories/<int:pk>/', AdminCategoryDetailView.as_view(), name='admin-category-detail'),

    # ── Product Management ──
    path('admin/products/', AdminProductListCreateView.as_view(), name='admin-product-list-create'),
    path('admin/products/<int:pk>/', AdminProductDetailView.as_view(), name='admin-product-detail'),

    # ── Product Variant Management ──
    path('admin/products/<int:product_id>/variants/', AdminProductVariantListCreateView.as_view(), name='admin-product-variant-create'),
    path('admin/variants/<int:pk>/', AdminProductVariantDetailView.as_view(), name='admin-variant-detail'),

    # ── Warehouse Stock & Double-Entry Ledger ──
    path('admin/inventory/adjust/', AdminInventoryStockAdjustView.as_view(), name='admin-inventory-adjust'),
    path('admin/inventory/transactions/', AdminInventoryTransactionListView.as_view(), name='admin-inventory-transactions'),
]