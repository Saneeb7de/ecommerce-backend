from django.urls import path
from .views import (
    CartDetailView,
    CartItemCreateView,
    CartItemDetailView,
    WishlistListView,
    WishlistToggleView,
)

urlpatterns = [
    # ─── Shopping Bag Routes ───────────────────────────────────────────
    path('cart', CartDetailView.as_view(), name='cart-detail'),
    path('cart/items', CartItemCreateView.as_view(), name='cart-item-create'),
    path('cart/items/<int:pk>', CartItemDetailView.as_view(), name='cart-item-detail'),

    # ─── Wishlist Routes ───────────────────────────────────────────────
    path('wishlist', WishlistListView.as_view(), name='wishlist-list'),
    path('wishlist/toggle', WishlistToggleView.as_view(), name='wishlist-toggle'),
]