from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from users.models import User
from catalog.models import Category, Product, ProductVariant, Inventory
from cart.models import Cart, CartItem, Wishlist


class CartAPITestCase(APITestCase):
    """
    Test suite for Shopping Cart:
    - Auto-creation of customer cart
    - Adding variant to cart (increments quantity if already exists)
    - Live warehouse stock guard (rejects quantities > available stock)
    - Updating item quantities and subtotal calculations
    - Cross-user security isolation (Customer A cannot edit Customer B's cart)
    - Clearing cart
    """

    def setUp(self):
        # 1. Customer A
        self.customer = User.objects.create_user(
            email='shopper.a@example.com',
            name='Shopper A',
            password='Password123!',
            role=User.Role.CUSTOMER,
            is_verified=True,
        )

        # 2. Customer B (for cross-user isolation testing)
        self.other_customer = User.objects.create_user(
            email='shopper.b@example.com',
            name='Shopper B',
            password='Password123!',
            role=User.Role.CUSTOMER,
            is_verified=True,
        )

        # 3. Product & Variant with 10 units in warehouse
        self.category = Category.objects.create(name="Audio", is_active=True)
        self.product = Product.objects.create(
            name="Sony WH-1000XM5",
            category=self.category,
            primary_image="https://example.com/sony.jpg",
            gallery_images=["https://example.com/s1.jpg", "https://example.com/s2.jpg"],
            is_published=True,
            is_deleted=False
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SONY-XM5-BLK",
            variant_name="Black",
            regular_price=Decimal("400.00"),
            sale_price=Decimal("350.00"),
            is_active=True
        )
        self.inventory = Inventory.objects.create(
            variant=self.variant,
            quantity=10,
            reserved_quantity=0
        )

    def test_customer_can_get_or_create_empty_cart(self):
        """
        GET /api/v1/cart automatically initializes an empty cart for the customer.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('cart-detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['total_items'], 0)
        self.assertEqual(response.data['data']['subtotal'], "0")
        self.assertTrue(response.data['data']['is_empty'])
        self.assertEqual(len(response.data['data']['items']), 0)

    def test_add_variant_to_cart_success(self):
        """
        POST /api/v1/cart/items adds an item to cart and computes subtotal.
        2 units @ $350.00 sale price = $700.00 line subtotal.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('cart-item-create')
        payload = {"variant_id": self.variant.id, "quantity": 2}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['quantity'], 2)
        self.assertEqual(response.data['data']['unit_price'], "350.00")
        self.assertEqual(response.data['data']['line_subtotal'], "700.00")
        self.assertEqual(response.data['cart']['total_items'], 2)
        self.assertEqual(response.data['cart']['subtotal'], "700.00")

    def test_adding_existing_variant_increments_quantity(self):
        """
        Business Rule: If a variant is already in the cart, adding it again
        increments the existing row's quantity instead of creating a duplicate row.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('cart-item-create')
        # First add: 2 units
        self.client.post(url, {"variant_id": self.variant.id, "quantity": 2}, format='json')
        # Second add: 3 more units
        response = self.client.post(url, {"variant_id": self.variant.id, "quantity": 3}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Should still be only 1 row in cart_items table:
        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 1)
        # But quantity should be 2 + 3 = 5:
        item = cart.items.first()
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.line_subtotal, Decimal("1750.00"))

    def test_add_to_cart_rejects_quantity_exceeding_warehouse_stock(self):
        """
        Stock Guard: Warehouse only has 10 units. Attempting to add 15 must fail with 400.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('cart-item-create')
        payload = {"variant_id": self.variant.id, "quantity": 15}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("stock", response.data['errors']['quantity'][0].lower())

    def test_update_cart_item_quantity(self):
        """
        PATCH /api/v1/cart/items/<id> updates quantity and recalculates subtotal.
        """
        self.client.force_authenticate(user=self.customer)
        # Pre-add item (qty = 1)
        cart = Cart.objects.create(user=self.customer)
        item = CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)
        patch_url = reverse('cart-item-detail', kwargs={'pk': item.id})
        response = self.client.patch(patch_url, {"quantity": 4}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(item.line_subtotal, Decimal("1400.00"))

    def test_customer_cannot_modify_another_customers_cart_item(self):
        """
        Security Isolation: Customer B cannot edit or delete Customer A's cart items.
        """
        # Create Customer A's cart item
        cart_a = Cart.objects.create(user=self.customer)
        item_a = CartItem.objects.create(cart=cart_a, variant=self.variant, quantity=1)
        # Login as Customer B and try to modify Customer A's item:
        self.client.force_authenticate(user=self.other_customer)
        patch_url = reverse('cart-item-detail', kwargs={'pk': item_a.id})
        response = self.client.patch(patch_url, {"quantity": 5}, format='json')
        # Must return 404 Not Found (safe security denial without leaking existence)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_cart_item(self):
        """
        DELETE /api/v1/cart/items/<id> removes specific line item.
        """
        self.client.force_authenticate(user=self.customer)
        cart = Cart.objects.create(user=self.customer)
        item = CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)
        delete_url = reverse('cart-item-detail', kwargs={'pk': item.id})
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())

    def test_clear_entire_cart(self):
        """
        DELETE /api/v1/cart removes all items from the customer's cart.
        """
        self.client.force_authenticate(user=self.customer)
        cart = Cart.objects.create(user=self.customer)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=2)
        clear_url = reverse('cart-detail')
        response = self.client.delete(clear_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(cart.items.count(), 0)


class WishlistAPITestCase(APITestCase):
    """
    Test suite for Customer Wishlist and Role/Auth Security:
    - Atomic bookmark toggle (Add on 1st click, Remove on 2nd click)
    - Viewing customer wishlist with product metadata & stock indicators
    - Customer privacy isolation
    - Strict authentication & customer role enforcement (IsCustomer)
    """
    def setUp(self):
        # 1. Customer A
        self.customer = User.objects.create_user(
            email='alice@example.com',
            name='Alice',
            password='Password123!',
            role=User.Role.CUSTOMER,
            is_verified=True,
        )
        # 2. Customer B
        self.other_customer = User.objects.create_user(
            email='bob@example.com',
            name='Bob',
            password='Password123!',
            role=User.Role.CUSTOMER,
            is_verified=True,
        )
        # 3. Store Admin (to test role restriction)
        self.admin = User.objects.create_user(
            email='admin.cart@example.com',
            name='Admin User',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_verified=True,
        )
        # 4. Catalog Product
        self.category = Category.objects.create(name="Audio", is_active=True)
        self.product = Product.objects.create(
            name="Bose QuietComfort Ultra",
            category=self.category,
            brand="Bose",
            primary_image="https://example.com/bose.jpg",
            gallery_images=["https://example.com/b1.jpg", "https://example.com/b2.jpg"],
            is_published=True,
            is_deleted=False
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="BOSE-QC-BLK",
            variant_name="Black",
            regular_price=Decimal("429.00"),
            is_active=True
        )
        Inventory.objects.create(variant=self.variant, quantity=15)


    def test_wishlist_toggle_adds_product_on_first_call(self):
        """
        POST /api/v1/wishlist/toggle adds the product if not currently bookmarked.
        Returns 201 Created with action='added'.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('wishlist-toggle')
        payload = {"product_id": self.product.id}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['action'], "added")
        self.assertTrue(response.data['is_wishlisted'])
        # Verify row in DB
        self.assertTrue(Wishlist.objects.filter(user=self.customer, product=self.product).exists())
    def test_wishlist_toggle_removes_product_on_second_call(self):
        """
        POST /api/v1/wishlist/toggle removes the product if already bookmarked.
        Returns 200 OK with action='removed'.
        """
        self.client.force_authenticate(user=self.customer)
        # Pre-create bookmark in DB
        Wishlist.objects.create(user=self.customer, product=self.product)
        url = reverse('wishlist-toggle')
        payload = {"product_id": self.product.id}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['action'], "removed")
        self.assertFalse(response.data['is_wishlisted'])
        # Verify row deleted from DB
        self.assertFalse(Wishlist.objects.filter(user=self.customer, product=self.product).exists())
    def test_customer_can_retrieve_wishlist_with_live_metadata(self):
        """
        GET /api/v1/wishlist returns serialized list of saved products with stock flags.
        """
        self.client.force_authenticate(user=self.customer)
        Wishlist.objects.create(user=self.customer, product=self.product)
        url = reverse('wishlist-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        item = response.data['data'][0]
        self.assertEqual(item['product_name'], "Bose QuietComfort Ultra")
        self.assertEqual(item['brand'], "Bose")
        self.assertTrue(item['is_in_stock'])
    def test_wishlist_privacy_isolation_between_customers(self):
        """
        Privacy Check: Bob cannot see Alice's bookmarked products.
        """
        # Alice bookmarks the product
        Wishlist.objects.create(user=self.customer, product=self.product)
        # Bob logs in and checks his wishlist
        self.client.force_authenticate(user=self.other_customer)
        url = reverse('wishlist-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Bob's wishlist must be empty
        self.assertEqual(response.data['count'], 0)
    def test_unauthenticated_request_is_rejected(self):
        """
        Anonymous users without a token must be rejected with 401 Unauthorized.
        """
        url = reverse('cart-detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    def test_admin_user_cannot_access_customer_cart(self):
        """
        Role Check: Store administrators (role=ADMIN) are rejected with 403 Forbidden
        from customer-only shopping bag endpoints (IsCustomer permission).
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('cart-detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)