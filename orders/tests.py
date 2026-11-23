from django.test import TestCase
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from users.models import User
from catalog.models import Category, Product, ProductVariant, Inventory
from cart.models import Cart, CartItem
# ⚠️ This import will FAIL initially because orders/services.py does NOT exist yet!
# That is the intentional, textbook "RED" stage of TDD.
from orders.services import PricingEngine



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
        self.cart = Cart.objects.create(user=self.customer)

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