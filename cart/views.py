from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from users.permissions import IsCustomer
from .models import Cart, CartItem, Wishlist
from .serializers import CartSerializer, CartItemSerializer, AddToCartSerializer, UpdateCartItemQuantitySerializer, WishlistItemSerializer, WishlistToggleSerializer
from catalog.utils import release_redis_stock, check_and_reserve_redis_stock

class CartDetailView(APIView):
    """
    GET    /api/v1/cart -> Returns customer's active shopping cart summary.
    DELETE /api/v1/cart -> Clears all items from the customer's cart.
    """
    permission_classes = [IsCustomer]

    def get(self, request):
        # Automatically creates an empty cart if customer doesn't have one yet
        cart, _ = Cart.objects.prefetch_related(
            'items__variant__product', 'items__variant__inventory'
        ).get_or_create(user=request.user)

        serializer = CartSerializer(cart)
        return Response({"success": True, "data": serializer.data},status=status.HTTP_200_OK)

    def delete(self, request):
        cart = Cart.objects.filter(user=request.user).first()
        
        if cart:
            # 1. Grab (sku, quantity) pairs before deleting
            items_to_release = list(cart.items.values_list('variant__sku', 'quantity'))

            # 2. Wipe the items from PostgreSQL
            cart.items.all().delete()

            # 3. Restore the stock in Redis so other shoppers can buy them
            for sku, quantity in items_to_release:
                release_redis_stock(sku, quantity)

        return Response(
            {
                "success": True, 
                "message": "Shopping cart emptied successfully."
            },
            status=status.HTTP_200_OK
        )


class CartItemCreateView(APIView):
    """
    POST /api/v1/cart/items
    Adds a variant to the cart.
    - If variant is already in the cart, increments existing quantity.
    - Enforces live warehouse stock checks.
    """

    permission_classes = [IsCustomer]

    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        variant = serializer.validated_data['variant']
        quantity = serializer.validated_data['quantity']

        cart, _ = Cart.objects.get_or_create(user=request.user)
        available_quantity = variant.inventory.available_quantity if hasattr(variant, 'inventory') else 0

        # Check if item already exists in this customer's cart
        cart_item = CartItem.objects.filter(cart=cart, variant=variant).first()

        if cart_item:
            new_qty = cart_item.quantity + quantity
            if new_qty > available_quantity:
                release_redis_stock(variant.sku, quantity)
                return Response(
                    {
                        "success": False,
                        "message": f"Cannot add {quantity} more. You already have {cart_item.quantity} in your cart, and only {available_quantity} units are available."
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.quantity = new_qty
            cart_item.save(update_fields=['quantity', 'updated_at'])
            message = f"Updated quantity for '{variant.sku}' to cart."
        else:
            cart_item = CartItem.objects.create(cart=cart, variant=variant, quantity=quantity)
            message = f"Added '{variant.sku}' to cart."

        Wishlist.objects.filter(user=request.user, product=variant.product).delete()

        return Response(
            {
                "success": True,
                "message": message,
                "id": cart_item.id,
                "data": CartItemSerializer(cart_item).data,
                "cart": CartSerializer(cart).data
            },
            status=status.HTTP_201_CREATED
        )


class CartItemDetailView(APIView):
    """
    PATCH  /api/v1/cart/items/<id> -> Updates quantity (+ / -) with live stock verification.
    DELETE /api/v1/cart/items/<id> -> Removes a specific item from the cart.
    """

    permission_classes = [IsCustomer]

    def get_object(self, request, pk):
        # Security: customer can only modify their OWN cart items
        return get_object_or_404(
            CartItem.objects.select_related('cart', 'variant__inventory', 'variant__product'),
            pk=pk,
            cart__user=request.user
        )

    def patch(self, request, pk):
        item = self.get_object(request, pk)
        serializer = UpdateCartItemQuantitySerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "success": False, "errors": serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        new_quantity = serializer.validated_data['quantity']
        available_quantity = item.variant.inventory.available_quantity if hasattr(item.variant, 'inventory') else 0

        if new_quantity > available_quantity:
            return Response(
                {
                    "success": False,
                    "message": f"Requested {new_quantity} units, but only {available_quantity} are available in stock."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 💡 Sync the difference to Redis:
        old_quantity = item.quantity
        diff = new_quantity - old_quantity
        if diff > 0:
            # User increased quantity -> Reserve more in Redis
            has_stock = check_and_reserve_redis_stock(item.variant.sku, diff, available_quantity)
            if not has_stock:
                return Response({"success": False, "message": "Cannot increase quantity; item just sold out!"}, status=status.HTTP_400_BAD_REQUEST)
        elif diff < 0:
            # User decreased quantity -> Return units back to Redis
            release_redis_stock(item.variant.sku, abs(diff))

        item.quantity = new_quantity
        item.save(update_fields=['quantity', 'updated_at'])

        return Response(
            {
                "success": True,
                "message": f"Quantity updated to {new_quantity}",
                "data": CartItemSerializer(item).data
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, pk):
        cart_item = self.get_object(request, pk)
        sku = cart_item.variant.sku
        qty = cart_item.quantity
        cart_item.delete()
        # 💡 Release stock back to Redis in-memory counter:
        release_redis_stock(sku, qty)
        cart, _ = Cart.objects.get_or_create(user=request.user)
        return Response(
            {
                "success": True,
                "message": "Cart item removed from cart.",
                "cart": CartSerializer(cart).data
            },
            status=status.HTTP_200_OK
        )


class WishlistListView(APIView):
    """
    GET /api/v1/wishlist -> Returns customer's bookmarked products.
    """
    permission_classes = [IsCustomer]

    def get(self, request):
        wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product__category').prefetch_related('product__variants__inventory').order_by('-created_at')
        serializer = WishlistItemSerializer(wishlist_items, many=True)
        return Response(
            {
                "success": True,
                "count": wishlist_items.count(),
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )


class WishlistToggleView(APIView):
    """
    POST /api/v1/wishlist/toggle
    Atomic toggle: Adds product to wishlist if not bookmarked; removes it if already bookmarked.
    """
    permission_classes = [IsCustomer]

    def post(self, request):
        serializer = WishlistToggleSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        product = serializer.validated_data['product']
        bookmark = Wishlist.objects.filter(user=request.user, product=product).first()

        if bookmark:
            bookmark.delete()
            return Response(
                {
                    "success": True,
                    "action": "removed",
                    "is_wishlisted": False,
                    "message": f"Removed '{product.name}' from your wishlist."
                },
                status=status.HTTP_200_OK
            )
        else:
            Wishlist.objects.create(user=request.user, product=product)
            return Response(
                {
                    "success": True,
                    "action": "added",
                    "is_wishlisted": True,
                    "message": f"Added '{product.name}' to your wishlist."
                },
                status=status.HTTP_201_CREATED
            )