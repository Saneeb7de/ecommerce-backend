from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from catalog.models import Category, Product, ProductVariant, Inventory, InventoryTransaction

class CategoryAPITestCase(APITestCase):
    """
    Test suite for Category CRUD, infinite nesting (recursive tree),
    and soft-deletion visibility.
    """

    def setUp(self):
        # 1. Create an Admin user for protected routes
        self.admin = User.objects.create_user(
            email='admin.test@example.com',
            name='Test Admin',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True,
            is_verified=True,
        )

        # 2. Create a normal Customer user to test unauthorized access
        self.customer = User.objects.create_user(
            email='customer.test@example.com',
            name='Test Customer',
            password='CustomerPassword123!',
            role=User.Role.CUSTOMER,
            is_verified=True,
        )

        # 3. Pre-create a parent category
        self.electronics = Category.objects.create(
            name="Electronics",
            description="All electronic devices",
            image_url="https://example.com/electronics.jpg",
            is_active=True
        )

    def test_admin_can_create_category_with_auto_slug(self):
        """
        Verify that an Admin can create a category, and the model
        automatically generates a clean URL-friendly slug.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-category-list-create')
        data = {
            "name": "Smartphones & Tablets",
            "description": "Mobile computing devices",
            "image_url": "https://example.com/smartphones.jpg"
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['name'], "Smartphones & Tablets")
        # Check auto-generated slug:
        self.assertEqual(response.data['data']['slug'], "smartphones-tablets")
        # Verify in database:
        self.assertTrue(Category.objects.filter(slug="smartphones-tablets").exists())

    def test_customer_cannot_create_category(self):
        """
        Security check: Normal customers must be rejected with 403 Forbidden.
        """
        self.client.force_authenticate(user=self.customer)
        url = reverse('admin-category-list-create')
        data = {"name": "Hacked Category"}

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


    def test_admin_can_create_nested_subcategory(self):
        """
        Verify infinite subcategory nesting via self-referencing parent foreign key.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-category-list-create')
        data = {
            "name": "Laptops",
            "parent": self.electronics.id,
            "description": "Work and gaming laptops"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['parent'], self.electronics.id)
        self.assertEqual(response.data['data']['parent_name'], "Electronics")

    def test_public_customer_can_view_recursive_category_tree(self):
        """
        Verify that GET /api/v1/catalog/categories/ returns root categories
        with their children nested inside the 'children' list.
        """
        # Create a child category under Electronics
        Category.objects.create(
            name="Audio",
            parent=self.electronics,
            is_active=True
        )

        url = reverse('customer-category-list')
        # Public request without login
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should return 1 root category (Electronics)
        self.assertEqual(response.data['count'], 1)
        root = response.data['data'][0]
        self.assertEqual(root['name'], "Electronics")
        # And its child (Audio) should be nested inside 'children'
        self.assertEqual(len(root['children']), 1)
        self.assertEqual(root['children'][0]['name'], "Audio")

    def test_soft_deleted_category_is_hidden_from_public(self):
        """
        Verify soft-delete: When admin deletes a category, is_active becomes False,
        and public customers can no longer see it.
        """
        self.client.force_authenticate(user=self.admin)
        delete_url = reverse('admin-category-detail', kwargs={'pk': self.electronics.id})

        # Admin deletes the category
        delete_res = self.client.delete(delete_url)
        self.assertEqual(delete_res.status_code, status.HTTP_204_NO_CONTENT)

        # Database check: Record is NOT wiped; is_active is now False
        self.electronics.refresh_from_db()
        self.assertFalse(self.electronics.is_active)

        # Public customer check: Empty list returned
        self.client.force_authenticate(user=None)
        public_url = reverse('customer-category-list')
        public_res = self.client.get(public_url)
        self.assertEqual(public_res.data['count'], 0)


class ProductAPITestCase(APITestCase):
    """
    Test suite for Master Product entity, 3-photo validation rule
    (1 primary + minimum 2 gallery = 3 total photos), uniqueness, and soft-delete.
    """

    def setUp(self):
        # 1. Admin user
        self.admin = User.objects.create_user(
            email='admin.prod@example.com',
            name='Product Admin',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_verified=True,
        )
        # 2. Category needed as Foreign Key
        self.category = Category.objects.create(
            name="Audio",
            is_active=True
        )
        # 3. Valid photo URLs for testing
        self.valid_payload = {
            "name": "Sony WH-1000XM5",
            "category": self.category.id,
            "brand": "Sony",
            "short_description": "Noise canceling headphones",
            "description": "Premium wireless headphones with dual processors.",
            "primary_image": "https://example.com/primary.jpg",
            "gallery_images": [
                "https://example.com/gallery1.jpg",
                "https://example.com/gallery2.jpg"
            ],
            "is_featured": True,
            "is_published": True
        }

    def test_admin_can_create_product_with_valid_3_photos(self):
        """
        Validates product creation when 1 primary + 2 gallery images are provided.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-list-create')
        response = self.client.post(url, self.valid_payload, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])
        self.assertEqual(response.data['data']['name'], "Sony WH-1000XM5")
        self.assertEqual(response.data['data']['slug'], "sony-wh-1000xm5")
        # Check DB
        self.assertTrue(Product.objects.filter(slug="sony-wh-1000xm5", is_deleted=False).exists())
    def test_product_creation_fails_with_less_than_2_gallery_images(self):
        """
        Business Rule: A product MUST have at least 2 gallery images.
        Submitting only 1 gallery image must be rejected with 400 Bad Request.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-list-create')
        invalid_payload = self.valid_payload.copy()
        # Provide only 1 gallery image instead of the required 2:
        invalid_payload['gallery_images'] = ["https://example.com/only_one.jpg"]
        response = self.client.post(url, invalid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gallery_images', response.data['errors'])
    def test_product_creation_fails_with_invalid_image_url(self):
        """
        Validator Rule: Every gallery image string must be a valid URL starting with 'http'.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-list-create')
        invalid_payload = self.valid_payload.copy()
        # Invalid string instead of an http URL:
        invalid_payload['gallery_images'] = ["https://example.com/valid.jpg", "invalid-plain-text-not-url"]
        response = self.client.post(url, invalid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gallery_images', response.data['errors'])
    def test_duplicate_active_product_name_is_rejected(self):
        """
        Uniqueness Rule: Active products cannot share the same name.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-list-create')
        # 1st creation succeeds
        self.client.post(url, self.valid_payload, format='json')
        # 2nd creation with exact same name must fail
        response = self.client.post(url, self.valid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', response.data['errors'])
    def test_admin_can_soft_delete_product(self):
        """
        Soft-Delete: Deleting a product marks is_deleted=True, preserving
        all database rows for historical invoice relations.
        """
        self.client.force_authenticate(user=self.admin)
        # Create product directly in DB
        product = Product.objects.create(
            name="Apple AirPods Max",
            category=self.category,
            primary_image="https://example.com/airpods.jpg",
            gallery_images=["https://example.com/g1.jpg", "https://example.com/g2.jpg"],
            is_published=True,
            is_deleted=False
        )
        delete_url = reverse('admin-product-detail', kwargs={'pk': product.id})
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify DB still has the row, but is_deleted is True
        product.refresh_from_db()
        self.assertTrue(product.is_deleted)


class ProductVariantAPITestCase(APITestCase):
    """
    Test suite for ProductVariant creation, pricing business rules,
    automatic warehouse inventory initialization, and partial PATCH updates.
    """

    def setUp(self):
        # 1. Admin user
        self.admin = User.objects.create_user(
            email='admin.var@example.com',
            name='Variant Admin',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_verified=True,
        )

        # 2. Category & Master Product
        self.category = Category.objects.create(name="Smartphones", is_active=True)
        self.product = Product.objects.create(
            name="Google Pixel 9",
            category=self.category,
            primary_image="https://example.com/pixel.jpg",
            gallery_images=["https://example.com/g1.jpg", "https://example.com/g2.jpg"],
            is_published=True,
            is_deleted=False
        )

        self.variant_payload = {
            "sku": "PIXEL9-BLK-128",
            "variant_name": "Obsidian Black / 128GB",
            "attributes": {"color": "Obsidian", "storage": "128GB"},
            "regular_price": "799.99",
            "sale_price": "749.99",
            "initial_stock": 50,
            "is_active": True
        }

    def test_admin_can_create_variant_with_auto_inventory_and_ledger(self):
        """
        Verify:
        1. Variant is created.
        2. Warehouse Inventory row is automatically initialized with stock = 50.
        3. Initial transaction ledger entry is created with balance_after = 50.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-variant-create', kwargs={'product_id': self.product.id})

        response = self.client.post(url, self.variant_payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['sku'], "PIXEL9-BLK-128")

        # 1. Check Variant in DB
        variant = ProductVariant.objects.get(sku="PIXEL9-BLK-128")
        self.assertEqual(variant.product, self.product)
        self.assertEqual(variant.regular_price, Decimal("799.99"))

        # 2. Check Auto-created Inventory
        self.assertTrue(hasattr(variant, 'inventory'))
        inventory = variant.inventory
        self.assertEqual(inventory.quantity, 50)
        self.assertEqual(inventory.available_quantity, 50)

        # 3. Check Auto-created Ledger Transaction
        txn = InventoryTransaction.objects.get(inventory=inventory)
        self.assertEqual(txn.transaction_type, InventoryTransaction.TransactionType.PURCHASE_RECEIPT)
        self.assertEqual(txn.quantity, 50)
        self.assertEqual(txn.balance_after, 50)
        self.assertEqual(txn.reference_id, "INITIAL_STOCK")

    def test_variant_creation_rejects_sale_price_greater_than_regular_price(self):
        """
        Pricing Integrity Rule: sale_price cannot be greater than regular_price.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-product-variant-create', kwargs={'product_id': self.product.id})
        invalid_payload = self.variant_payload.copy()
        # Invalid: sale price $850 > regular price $799.99
        invalid_payload['sale_price'] = "850.00"

        response = self.client.post(url, invalid_payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sale_price', response.data['errors'])

    def test_admin_can_partial_patch_variant_price_safely(self):
        """
        PATCH validation: Sending only 'sale_price' without regular_price
        must fall back to the existing database regular_price and succeed.
        """
        self.client.force_authenticate(user=self.admin)
        # Pre-create a variant
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="PIXEL9-HAZEL-128",
            variant_name="Hazel / 128GB",
            regular_price=Decimal("799.99"),
            sale_price=Decimal("749.99"),
            is_active=True
        )
        Inventory.objects.create(variant=variant, quantity=30)

        patch_url = reverse('admin-variant-detail', kwargs={'pk': variant.id})

        # Partial update with only sale_price
        patch_res = self.client.patch(patch_url, {"sale_price": "699.99"}, format='json')

        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        variant.refresh_from_db()
        self.assertEqual(variant.sale_price, Decimal("699.99"))

    def test_admin_can_list_variants_of_product(self):
        """
        GET /api/v1/admin/products/<id>/variants/ returns all variants of that product.
        """
        self.client.force_authenticate(user=self.admin)
        ProductVariant.objects.create(
            product=self.product,
            sku="PIXEL9-ROSE-128",
            variant_name="Rose / 128GB",
            regular_price=Decimal("799.99"),
            is_active=True
        )

        url = reverse('admin-product-variant-create', kwargs={'product_id': self.product.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

    def test_admin_can_deactivate_variant(self):
        """
        DELETE /api/v1/admin/variants/<id>/ deactivates the variant (is_active = False).
        """
        self.client.force_authenticate(user=self.admin)
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="PIXEL9-OLD-SKU",
            variant_name="Discontinued Edition",
            regular_price=Decimal("799.99"),
            is_active=True
        )

        delete_url = reverse('admin-variant-detail', kwargs={'pk': variant.id})
        response = self.client.delete(delete_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        variant.refresh_from_db()
        self.assertFalse(variant.is_active)


class InventoryLedgerAPITestCase(APITestCase):
    """
    Test suite for Fintech / Double-Entry Inventory Ledger:
    - Atomic stock adjustments
    - Pessimistic locking (select_for_update)
    - Protection against warehouse stock dipping below 0 or below reserved orders
    - Historical ledger audit trail
    """

    def setUp(self):
        # 1. Admin user
        self.admin = User.objects.create_user(
            email='admin.inv@example.com',
            name='Inventory Admin',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_verified=True,
        )

        # 2. Category, Product, Variant, and initial Inventory (stock = 50)
        self.category = Category.objects.create(name="Wearables", is_active=True)
        self.product = Product.objects.create(
            name="Apple Watch Ultra",
            category=self.category,
            primary_image="https://example.com/watch.jpg",
            gallery_images=["https://example.com/g1.jpg", "https://example.com/g2.jpg"],
            is_published=True,
            is_deleted=False
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="WATCH-ULTRA-ORG",
            variant_name="Titanium / Orange Loop",
            regular_price=Decimal("799.00"),
            is_active=True
        )
        self.inventory = Inventory.objects.create(
            variant=self.variant,
            quantity=50,
            reserved_quantity=0,
            low_stock_threshold=5
        )
        # Record initial ledger entry
        InventoryTransaction.objects.create(
            inventory=self.inventory,
            transaction_type=InventoryTransaction.TransactionType.PURCHASE_RECEIPT,
            quantity=50,
            balance_after=50,
            reference_id="INIT-PO-01",
            description="Initial batch received"
        )

    def test_stock_adjustment_positive_writes_ledger_and_updates_quantity(self):
        """
        Supplier restock (+25 units):
        - Increases warehouse quantity from 50 to 75.
        - Writes an immutable InventoryTransaction record with balance_after = 75.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-inventory-adjust')
        payload = {
            "sku": "WATCH-ULTRA-ORG",
            "adjustment_quantity": 25,
            "transaction_type": "PURCHASE_RECEIPT",
            "reference_id": "PO-2026-002",
            "description": "Supplier restock shipment"
        }

        response = self.client.post(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 75)

        # Verify new ledger entry created
        latest_txn = InventoryTransaction.objects.filter(inventory=self.inventory).order_by('-created_at').first()
        self.assertEqual(latest_txn.quantity, 25)
        self.assertEqual(latest_txn.balance_after, 75)
        self.assertEqual(latest_txn.reference_id, "PO-2026-002")

    def test_stock_adjustment_negative_writes_ledger_and_updates_quantity(self):
        """
        Damaged stock removal (-10 units):
        - Reduces warehouse quantity from 50 to 40.
        - Writes an immutable InventoryTransaction record with balance_after = 40.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-inventory-adjust')
        payload = {
            "sku": "WATCH-ULTRA-ORG",
            "adjustment_quantity": -10,
            "transaction_type": "MANUAL_ADJUSTMENT",
            "reference_id": "DMG-LOG-01",
            "description": "Damaged goods written off"
        }

        response = self.client.post(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 40)

        latest_txn = InventoryTransaction.objects.filter(inventory=self.inventory).order_by('-created_at').first()
        self.assertEqual(latest_txn.quantity, -10)
        self.assertEqual(latest_txn.balance_after, 40)

    def test_stock_adjustment_rejects_deduction_below_zero(self):
        """
        Safety Check: Cannot deduct more than warehouse stock on hand.
        Deducting 60 units when only 50 exist must fail with 400 Bad Request.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-inventory-adjust')
        payload = {
            "sku": "WATCH-ULTRA-ORG",
            "adjustment_quantity": -60
        }

        response = self.client.post(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Quantity remains intact at 50:
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 50)

    def test_stock_adjustment_rejects_deduction_below_reserved_orders(self):
        """
        Safety Check: Cannot deduct stock below reserved quantities locked in checkouts.
        If total = 50, reserved = 35, available is 15. Deducting 20 units must fail.
        """
        self.inventory.reserved_quantity = 35
        self.inventory.save()

        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-inventory-adjust')
        # Total would become 30 (which is less than 35 reserved):
        payload = {
            "sku": "WATCH-ULTRA-ORG",
            "adjustment_quantity": -20
        }

        response = self.client.post(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reserved", response.data['message'].lower())

    def test_admin_can_view_transaction_ledger_audit_trail(self):
        """
        GET /api/v1/admin/inventory/transactions/?sku=...
        Returns chronological double-entry ledger entries for auditing.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-inventory-transactions')

        response = self.client.get(url, {'sku': 'WATCH-ULTRA-ORG'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['variant_sku'], 'WATCH-ULTRA-ORG')


class CustomerCatalogDiscoveryTestCase(APITestCase):
    """
    Test suite for Customer-Facing Catalog Discovery:
    - Dynamic SQL price annotation (cheapest effective price)
    - Full-text search (?search=...)
    - Category filtering (?category=...)
    - Price sorting (?sort=price_asc, ?sort=price_desc)
    - Featured products endpoint (/featured/)
    - Full product detail retrieval by slug (/products/<slug>/)
    """

    def setUp(self):
        # 1. Categories
        self.cat_audio = Category.objects.create(name="Audio & Sound", is_active=True)
        self.cat_phones = Category.objects.create(name="Smartphones", is_active=True)

        # 2. Product 1: Sony Headphones (Featured, $349.99 sale price)
        self.sony = Product.objects.create(
            name="Sony WH-1000XM5",
            category=self.cat_audio,
            brand="Sony",
            short_description="Noise canceling headphones",
            description="High fidelity wireless sound with active noise cancellation.",
            primary_image="https://example.com/sony_p.jpg",
            gallery_images=["https://example.com/s1.jpg", "https://example.com/s2.jpg"],
            is_featured=True,
            is_published=True,
            is_deleted=False
        )
        self.sony_variant = ProductVariant.objects.create(
            product=self.sony,
            sku="SONY-XM5-BLK",
            variant_name="Black",
            regular_price=Decimal("399.99"),
            sale_price=Decimal("349.99"),
            is_active=True
        )
        Inventory.objects.create(variant=self.sony_variant, quantity=50)

        # 3. Product 2: Apple iPhone 15 (Non-featured, $799.00 regular price)
        self.iphone = Product.objects.create(
            name="Apple iPhone 15",
            category=self.cat_phones,
            brand="Apple",
            short_description="Latest Apple flagship",
            description="Super Retina XDR display with Dynamic Island.",
            primary_image="https://example.com/iphone_p.jpg",
            gallery_images=["https://example.com/i1.jpg", "https://example.com/i2.jpg"],
            is_featured=False,
            is_published=True,
            is_deleted=False
        )
        self.iphone_variant = ProductVariant.objects.create(
            product=self.iphone,
            sku="IPHONE-15-BLU",
            variant_name="Blue / 128GB",
            regular_price=Decimal("799.00"),
            sale_price=None,
            is_active=True
        )
        Inventory.objects.create(variant=self.iphone_variant, quantity=25)

    def test_customer_product_list_calculates_cheapest_effective_price_in_sql(self):
        """
        Postgres optimization check: Customer product card displays
        'starting_price' correctly derived from the cheapest active variant.
        Sony sale price ($349.99) < regular price ($399.99).
        """
        url = reverse('customer-product-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        sony_card = next(p for p in results if p['id'] == self.sony.id)
        # Should show the discounted sale price as starting price:
        self.assertEqual(sony_card['starting_price'], "349.99")
        self.assertTrue(sony_card['is_in_stock'])

        iphone_card = next(p for p in results if p['id'] == self.iphone.id)
        # Should show regular price when sale_price is None:
        self.assertEqual(iphone_card['starting_price'], "799.00")

    def test_customer_can_search_products_by_text(self):
        """
        ?search=sony returns only the Sony headphones, filtering out Apple.
        """
        url = reverse('customer-product-list')
        response = self.client.get(url, {'search': 'Sony'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['name'], "Sony WH-1000XM5")

    def test_customer_can_filter_products_by_category(self):
        """
        ?category=audio-sound filters by category slug.
        """
        url = reverse('customer-product-list')
        response = self.client.get(url, {'category': self.cat_audio.slug})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.sony.id)

    def test_customer_can_sort_products_by_price(self):
        """
        ?sort=price_asc lists cheaper products ($349.99) before expensive ones ($799.00).
        ?sort=price_desc lists expensive products first.
        """
        url = reverse('customer-product-list')

        # Price Ascending:
        res_asc = self.client.get(url, {'sort': 'price_asc'})
        self.assertEqual(res_asc.data['results'][0]['id'], self.sony.id)
        self.assertEqual(res_asc.data['results'][1]['id'], self.iphone.id)

        # Price Descending:
        res_desc = self.client.get(url, {'sort': 'price_desc'})
        self.assertEqual(res_desc.data['results'][0]['id'], self.iphone.id)
        self.assertEqual(res_desc.data['results'][1]['id'], self.sony.id)

    def test_customer_can_retrieve_featured_products(self):
        """
        GET /api/v1/catalog/products/featured/
        Returns only products where is_featured=True.
        """
        url = reverse('customer-featured-products')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Only Sony is featured:
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['data'][0]['id'], self.sony.id)

    def test_customer_can_retrieve_product_detail_by_slug(self):
        """
        GET /api/v1/catalog/products/<slug>/
        Returns full specifications, category metadata, and all active variants with stock.
        """
        url = reverse('customer-product-detail', kwargs={'slug': self.sony.slug})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_data = response.data['data']
        self.assertEqual(product_data['name'], "Sony WH-1000XM5")
        self.assertEqual(product_data['category']['name'], "Audio & Sound")
        # Check variants list
        self.assertEqual(len(product_data['variants']), 1)
        variant_data = product_data['variants'][0]
        self.assertEqual(variant_data['sku'], "SONY-XM5-BLK")
        self.assertEqual(variant_data['inventory']['available_quantity'], 50)