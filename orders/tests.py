from django.test import TestCase
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from users.models import User
from catalog.models import Category, Product, ProductVariant, Inventory
from cart.models import Cart, CartItem
# ⚠️ This import will FAIL initially because orders/services.py does NOT exist yet!
# That is the intentional, textbook "RED" stage of TDD.
from orders.services import PricingEngine
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from users.models import Address
from orders.models import Order, Wallet, WalletTransaction
from django.urls import reverse



class PricingEngineTDDTestCase(TestCase):
    """
    Unit test suite for the Checkout Pricing & Tax Calculation Engine.
    Written BEFORE implementing orders/services.py (TDD Approach).
    """

    def setUp(self):
        #1. Test Customer
        self.customer = User.objects.create_user(
            email='pricing.test@example.com',
            name='Test Shopper',
            password='Password123!',
            role=User.Role.CUSTOMER,
            is_verified=True
        )

        # 2. Category & Product
        self.category = Category.objects.create(name='Electronics', is_active=True)
        self.product = Product.objects.create(
            name='Wireless Headset',
            category=self.category,
            primary_image='https://example.com/headset.jpg',
            gallery_images=['https://example.com/h1.jpg', 'https://example.com/h2.jpg'],
            is_published=True,
            is_deleted=False
        )

        # 3. Two variant with inventory
        # Variant A: ₹400.00 each, 10 units in stock
        self.variant_a = ProductVariant.objects.create(
            product=self.product,
            sku='HEADSET-BLK',
            variant_name='Black',
            regular_price=Decimal('500.00'),
            sale_price=Decimal('400.00'),
            is_active=True
        )
        self.inv_a = Inventory.objects.create(variant=self.variant_a, quantity=10)

        # Variant B: ₹250.00 each, 10 units in stock
        self.variant_b = ProductVariant.objects.create(
            product=self.product,
            sku='HEADSET-WHT',
            variant_name='White',
            regular_price=Decimal('250.00'),
            sale_price=Decimal('0.00'),  # Regular price applies
            is_active=True
        )
        self.inv_b = Inventory.objects.create(variant=self.variant_b, quantity=10)

        # 4. Shopping Cart
        self.cart = Cart.objects.get_or_create(user=self.customer)[0]

    def test_tiered_shipping_fee_below_threshold(self):
        """
        Rule: Subtotal < ₹1,000 charges standard shipping fee of ₹100.
        Variant B: 2 units * ₹250.00 = ₹500.00 subtotal.
        """

        CartItem.objects.create(cart=self.cart, variant=self.variant_b, quantity=2)

        pricing = PricingEngine.calculate_cart_totals(self.cart)

        self.assertEqual(pricing['subtotal'], Decimal('500.00'))
        self.assertEqual(pricing['shipping_fee'], Decimal('100.00')) # Below ₹1000 threshold

    def test_tiered_shipping_fee_free_shipping_above_threshold(self):
        """
        Rule: Subtotal >= ₹1,000 gets FREE shipping (₹0.00).
        Variant A: 3 units * ₹400.00 = ₹1,200.00 subtotal.
        """

        CartItem.objects.create(cart=self.cart, variant=self.variant_a, quantity=3)


        pricing = PricingEngine.calculate_cart_totals(self.cart)

        self.assertEqual(pricing['subtotal'], Decimal('1200.00'))
        self.assertEqual(pricing['shipping_fee'], Decimal('0.00'))  # Free shipping!


    def test_gst_tax_and_grand_total_calculation(self):
        """
        Rule: 18% GST on taxable subtotal.
        Subtotal: ₹1,000.00
        Shipping: ₹0.00 (>=1000)
        Tax: (18% of 1000): ₹180
        Grand Total: 1000 + 0 + 180 = ₹1,180
        """

        CartItem.objects.create(cart=self.cart, variant=self.variant_b, quantity=4)  # 4 * 250 = 1000

        pricing = PricingEngine.calculate_cart_totals(self.cart)

        self.assertEqual(pricing['subtotal'], Decimal('1000.00'))
        self.assertEqual(pricing['tax_amount'], Decimal('180.00'))
        self.assertEqual(pricing['grand_total'], Decimal('1180.00'))


    def test_coupon_discount_reduces_subtotal_and_tax(self):
        """
        Rule: Coupon 'WELCOME10' gives 10 % discount on subtotal.
        Subtotal: ₹1000.00
        Discount (10%): ₹100.00
        Taxable amount: ₹900.00
        Tax: (18% of 900): ₹162.00
        Shipping (subtotal >=1000): ₹0.00
        Grand Total: 1000 - 100 + 0 + 162 = ₹1062.00
        """
        CartItem.objects.create(cart=self.cart, variant=self.variant_b, quantity=4)

        pricing = PricingEngine.calculate_cart_totals(self.cart, coupon_code='WELCOME10')

        self.assertEqual(pricing['discount_amount'], Decimal('100.00'))
        self.assertEqual(pricing['tax_amount'], Decimal('162.00'))
        self.assertEqual(pricing['grand_total'], Decimal('1062.00'))

    def test_empty_cart_raises_validation_error(self):
        """
        Rule: Cannot calculate totals on an empty cart. Must raise ValidationError.
        """

        with self.assertRaises(ValidationError) as ctx:
            PricingEngine.calculate_cart_totals(self.cart)
        self.assertIn("Your cart is empty", str(ctx.exception))

    def test_out_of_stock_item_in_cart_raises_validation_error(self):
        """
        Rule: If warehouse available stock is less than cart quantity.
        the calculation must reject checkout with ValidationError.
        """
        # Warehouse only has 10 units. Attempts calculate for 15 units.
        CartItem.objects.create(cart=self.cart, variant=self.variant_a, quantity=15)

        with self.assertRaises(ValidationError) as ctx:
            PricingEngine.calculate_cart_totals(self.cart)
        self.assertIn("insufficient stock", str(ctx.exception).lower())


    def test_cod_eligibility_flag(self):
        """
        Rule: Orders exceeding ₹50,000 are not eligible for Cash on Delivery.
        """

        # 1 unit of variant A = ₹400 -> COD eligible: True
        CartItem.objects.create(cart=self.cart, variant=self.variant_a, quantity=1)
        pricing = PricingEngine.calculate_cart_totals(self.cart)
        self.assertTrue(pricing['is_cod_eligible'])

    def test_multiple_different_items_in_cart_calculates_all_items(self):
        """
        Regression Test: Ensure calculation processes ALL items in the cart,
        not just the first item.
        Variant A (1 unit * ₹400 = ₹400) + Variant B (2 units * ₹250 = ₹500) = ₹900 subtotal.
        """
        CartItem.objects.create(cart=self.cart, variant=self.variant_a, quantity=1)
        CartItem.objects.create(cart=self.cart, variant=self.variant_b, quantity=2)

        pricing = PricingEngine.calculate_cart_totals(self.cart)

        self.assertEqual(len(pricing['items']), 2)
        self.assertEqual(pricing['total_items'], 3)
        self.assertEqual(pricing['subtotal'], Decimal('900.00'))
        self.assertEqual(pricing['shipping_fee'], Decimal('100.00'))  # < 1000


    def test_database_coupon_validation_and_discount(self):
        """
        Verify that a dynamic Coupon created in the database is correctly
        applied by PricingEngine.
        """
        from orders.models import Coupon
        from django.utils import timezone
        import datetime

        # Create a 20% off database coupon
        coupon = Coupon.objects.create(
            code='SAVE20',
            discount_type=Coupon.DiscountType.PERCENTAGE,
            discount_value=Decimal('20.00'),
            min_order_amount=Decimal('400.00'),
            start_date=timezone.now() - datetime.timedelta(days=1),
            end_date=timezone.now() + datetime.timedelta(days=10),
            is_active=True
        )

        # Cart: 2 units of Variant A (2 * ₹400 = ₹800 subtotal)
        CartItem.objects.create(cart=self.cart, variant=self.variant_a, quantity=2)

        pricing = PricingEngine.calculate_cart_totals(self.cart, coupon_code='SAVE20', user=self.customer)

        # 20% of ₹800 = ₹160 discount
        self.assertEqual(pricing['discount_amount'], Decimal('160.00'))
        self.assertEqual(pricing['applied_coupon'], 'SAVE20')

    def test_wallet_credit_and_debit_ledger(self):
        """
        Verify that Wallet credit() adds balance and debit() deducts balance
        with double-entry audit records.
        """
        from orders.models import Wallet, WalletTransaction

        wallet, _ = Wallet.objects.get_or_create(user=self.customer)
        self.assertEqual(wallet.balance, Decimal('0.00'))

        # 1. Credit ₹1000
        wallet.credit(Decimal('1000.00'), source=WalletTransaction.TransactionSource.TOP_UP, description="Initial Top-up")
        self.assertEqual(wallet.balance, Decimal('1000.00'))
        self.assertEqual(wallet.transactions.count(), 1)

        # 2. Debit ₹300
        wallet.debit(Decimal('300.00'), source=WalletTransaction.TransactionSource.ORDER_PAYMENT, description="Payment for Order")
        self.assertEqual(wallet.balance, Decimal('700.00'))
        self.assertEqual(wallet.transactions.count(), 2)


from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from users.models import Address
from orders.models import Order, Wallet, WalletTransaction


@override_settings(ALLOWED_HOSTS=['*'])
class OrderWalletLifecycleAPITestCase(APITestCase):
    """
    Critical Path: Full Order Placement via Wallet & Order Cancellation Refund.
    """

    def setUp(self):
        # 1. Verified Customer with Address & Pre-funded Wallet
        self.customer = User.objects.create_user(
            email='shopper.wallet@example.com',
            name='Wallet Shopper',
            password='SecurePassword123!',
            role=User.Role.CUSTOMER,
            is_verified=True
        )
        self.address = Address.objects.create(
            user=self.customer,
            recipient_name='Wallet Shopper',
            phone_number='9876543210',
            street_address='123 Test Street',
            city='Bengaluru',
            state='Karnataka',
            postal_code='560001',
            country='IN',
            is_default=True
        )
        # Fund Wallet with ₹5,000.00
        self.wallet = Wallet.objects.get_or_create(user=self.customer)[0]
        self.wallet.credit(
            amount=Decimal('5000.00'),
            source=WalletTransaction.TransactionSource.TOP_UP,
            description="Pre-test wallet top-up"
        )

        # 2. Category, Product, Variant (₹500 each, 20 initial stock)
        self.category = Category.objects.create(name='Audio Gear', is_active=True)
        self.product = Product.objects.create(
            category=self.category,
            name='Studio Wireless Headphones',
            brand='AudioPro',
            description='High performance studio monitors.',
            primary_image='https://images.unsplash.com/photo-1505740420928-5e560c06d30e',
            gallery_images=['https://images.unsplash.com/photo-1', 'https://images.unsplash.com/photo-2'],
            is_published=True
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku='STUDIO-HP-BLK',
            variant_name='Matte Black',
            regular_price=Decimal('500.00'),
            sale_price=Decimal('500.00'),
            is_active=True
        )
        self.inventory = Inventory.objects.create(
            variant=self.variant,
            quantity=20,
            reserved_quantity=0
        )

        # 3. Customer Cart with 2 items (Subtotal = ₹1,000)
        self.cart = Cart.objects.get_or_create(user=self.customer)[0]
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            variant=self.variant,
            quantity=2
        )

        self.client.force_authenticate(user=self.customer)

    def test_wallet_checkout_and_cancellation_refund(self):
        # --- STEP 1: PLACE ORDER VIA WALLET ---
        create_url = reverse('order-create-list')
        order_payload = {
            "address_id": self.address.id,
            "payment_method": "WALLET"
        }
        response = self.client.post(create_url, order_payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        order_data = response.data['data']
        order_number = order_data['order_number']

        # Verify Order state in DB
        order = Order.objects.get(order_number=order_number)
        self.assertEqual(order.status, Order.OrderStatus.CONFIRMED)
        self.assertEqual(order.payment_status, Order.PaymentStatus.COMPLETED)
        self.assertEqual(order.payment_method, Order.PaymentMethod.WALLET)

        # Verify Wallet was debited by exactly the grand total
        self.wallet.refresh_from_db()
        expected_balance = Decimal('5000.00') - order.grand_total
        self.assertEqual(self.wallet.balance, expected_balance)

        # Verify Stock decremented from 20 -> 18
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 18)

        # Verify Cart was cleared
        self.assertEqual(self.cart.items.count(), 0)

        # --- STEP 2: CANCEL ORDER & VERIFY AUTOMATED REFUND ---
        cancel_url = reverse('order-cancel', kwargs={'identifier': order_number})
        cancel_payload = {"reason": "Customer requested cancellation before shipment"}
        cancel_response = self.client.post(cancel_url, cancel_payload, format='json')

        self.assertEqual(cancel_response.status_code, status.HTTP_200_OK)

        # Verify Order state changed to CANCELLED & REFUNDED
        order.refresh_from_db()
        self.assertEqual(order.status, Order.OrderStatus.CANCELLED)
        self.assertEqual(order.payment_status, Order.PaymentStatus.REFUNDED)

        # Verify Wallet was refunded back to ₹5,000.00
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('5000.00'))

        # Verify Stock was restored from 18 -> 20
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)