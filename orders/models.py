from django.db import models
import uuid
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from decimal import ROUND_HALF_UP


def generate_order_number():
    """Generates unique, human-readable order number: ORD-YYYYMMDD-XXXXXX"""
    data_str = timezone.now().strftime('%Y%m%d')
    random_str = uuid.uuid4().hex[:6].upper()
    return f"ORD-{data_str}-{random_str}"

#order model
class Order(models.Model):
    """
    Master Order Entity.
    Tracks financial breakdown, frozen delivery address, and fulfillment address lifecycle.
    """

    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        SHIPPED = 'SHIPPED', 'Shipped'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'
        RETURN_REQUESTED = 'RETURN_REQUESTED', 'Return Requested'
        RETURNED = 'RETURNED', 'Returned'

    class PaymentMethod(models.TextChoices):
        COD = 'COD', 'Cash on Delivery'
        RAZORPAY = 'RAZORPAY', 'Razorpay Online'
        WALLET = 'WALLET', 'Store Wallet'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'


    # 🚦 State Machine: Permitted order status transitions
    PERMITTED_STATUS_TRANSITIONS = {
        OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
        OrderStatus.CONFIRMED: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
        OrderStatus.SHIPPED: [OrderStatus.OUT_FOR_DELIVERY],
        OrderStatus.OUT_FOR_DELIVERY: [OrderStatus.DELIVERED],
        OrderStatus.DELIVERED: [OrderStatus.RETURN_REQUESTED],
        OrderStatus.RETURN_REQUESTED: [OrderStatus.RETURNED, OrderStatus.DELIVERED],
        OrderStatus.CANCELLED: [],  # Terminal state
        OrderStatus.RETURNED: [],   # Terminal state
    }

    order_number = models.CharField(max_length=64, unique=True, db_index=True, default=generate_order_number, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders', db_index=True)
    status = models.CharField(max_length=30, choices=OrderStatus.choices, default=OrderStatus.PENDING, db_index=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COD, db_index=True)
    coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    # ── Financial Breakdown ──
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    # ── Anti-Tamper Delivery Address Snapshot ──
    # Freezes recipient name, phone, address_line_1, city, state, postal_code at purchase
    shipping_address_snapshot = models.JSONField(help_text="Immutable snapshot of the delivery address at the moment of checkout.")
    # ── Fulfillment & Tracking ──
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    carrier_name = models.CharField(max_length=100, blank=True, null=True)
    # ── Cancellation Tracking ──
    cancellation_reason = models.TextField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    # ── Timestamps ──
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'
        ordering = ['-created_at']  # Latest-first ordering
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'

    def __str__(self):
        return f"{self.order_number} ({self.user.email}) - {self.status}"

    @property
    def is_cancellable(self):  # 👈 added underscore
        """Customers can cancel only before dispatch."""
        return self.status in [self.OrderStatus.PENDING, self.OrderStatus.CONFIRMED]
    @property
    def is_returnable(self):
        """Delivered orders can be returned within 7 days"""
        if self.status != self.OrderStatus.DELIVERED:
            return False
        delta = timezone.now() - self.updated_at
        return delta.days <= 7
    def can_transition_to(self, new_status):
        """Validates if status change adheres to the state machine."""
        allowed = self.PERMITTED_STATUS_TRANSITIONS.get(self.status, [])
        return new_status in allowed

class OrderItem(models.Model):
    """
    Individual line items attached to an Order.
    Includes anti-tamper snapshots to preserve historical purchase integrity.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    # ── Snapshots ──
    product_name_snapshot = models.CharField(max_length=255)
    variant_name_snapshot = models.CharField(max_length=255)
    sku_snapshot = models.CharField(max_length=100)
    primary_image_snapshot = models.URLField(max_length=500, blank=True, null=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_items'
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'

    def __str__(self):
        return f"{self.quantity}x {self.sku_snapshot} ({self.order.order_number})"


class PaymentTransaction(models.Model):
    """
    Audit log for payment attempts (supports Razorpay and COD).
    Allow retrying failed payments without duplicate the parent Order. 
    """

    class TransactionStatus(models.TextChoices):
        INITIATED = 'INITIATED', 'Initiated'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    payment_method = models.CharField(max_length=20, choices=Order.PaymentMethod.choices, default=Order.PaymentMethod.RAZORPAY)
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.INITIATED, db_index=True)

    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'order_payment_transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment {self.id} for {self.order.order_number} - {self.status}"


class OrderTrackingMilestone(models.Model):
    """
    Timeline tracking: records the chronological progress of an order.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='milestones')
    status = models.CharField(max_length=30, choices=Order.OrderStatus.choices)
    notes = models.CharField(max_length=100, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_tracking_milestones'
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.order.order_number} reached {self.status} at {self.timestamp}"


class ReturnRequest(models.Model):
    class ReturnStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
    
    class ReturnType(models.TextChoices):
        REFUND = 'REFUND', 'Return & Refund to Wallet'
        REPLACEMENT = 'REPLACEMENT', 'Replacement Unit'

    class ReturnReason(models.TextChoices):
        DAMAGED = 'DAMAGED', 'Damaged in transit'
        DEFECTIVE = 'DEFECTIVE', 'Defective / Not functioning'
        WRONG_ITEM = 'WRONG_ITEM', 'Wrong product delivered'
        SIZE_FIT = 'SIZE_FIT', 'Size / Fit issue'
        CHANGED_MIND = 'CHANGED_MIND', 'Changed mind / Not needed'
        OTHER = 'OTHER', 'Other reason'

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='return_request')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='return_requests')
    return_type = models.CharField(max_length=20, choices=ReturnType.choices, default=ReturnType.REFUND)
    reason_type = models.CharField(max_length=30, choices=ReturnReason.choices, default=ReturnReason.DAMAGED)
    reason = models.TextField(help_text="Mandatory reason for return request.")
    status = models.CharField(max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.PENDING, db_index=True)
    admin_notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'order_return_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"Return for {self.order.order_number} - {self.status}"



class Wallet(models.Model):
    """
    Customer In-App Store Wallet.
    Supports atomic credits (returns/refunds/top-ups) and debits (order payments).
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wallets'
        verbose_name = 'Wallet'
        verbose_name_plural = 'Wallets'

    def __str__(self):
        return f"Wallet ({self.user.email}) - ₹{self.balance}"

    def credit(self, amount, source, description='', reference_id=''):
        """ Atomically credits money to wallet and records an immutable ledger entry."""

        if amount <= 0:
            raise ValidationError("Credit amount must be greater than zero.")

        self.balance += amount
        self.save(update_fields=['balance', 'updated_at'])

        return WalletTransaction.objects.create(
            wallet=self,
            amount=amount,
            transaction_type=WalletTransaction.TransactionType.CREDIT,
            source=source,
            description=description,
            reference_id=reference_id,
            balance_after=self.balance
        )

    def debit(self, amount, source, description='', reference_id=''):
        """Atomically debits money from wallet to pay for an order."""
        if amount <= 0:
            raise ValidationError("Debit amount must be greater than zero")
        if self.balance < amount:
            raise ValidationError(f"Insufficient wallet balance. Available: ₹{self.balance}, Required: ₹{amount}")
        self.balance -= amount
        self.save(update_fields=['balance', 'updated_at'])
        return WalletTransaction.objects.create(
            wallet=self,
            amount=amount,
            transaction_type=WalletTransaction.TransactionType.DEBIT,
            source=source,
            description=description,
            reference_id=reference_id,
            balance_after=self.balance
        )

class WalletTransaction(models.Model):
    """Double entry immutable audit ledger for every wallet movement."""
    class TransactionType(models.TextChoices):
        CREDIT = 'CREDIT', 'Credit (+)'
        DEBIT = 'DEBIT', 'Debit (-)'

    class TransactionSource(models.TextChoices):
        TOP_UP = 'TOP_UP', 'Wallet Top-Up'
        ORDER_PAYMENT = 'ORDER_PAYMENT', 'Order Payment'
        ORDER_REFUND = 'ORDER_REFUND', 'Order Refund / Return'
        ADMIN_ADJUSTMENT = 'ADMIN_ADJUSTMENT', 'Admin Adjustment'

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    source = models.CharField(max_length=30, choices=TransactionSource.choices)
    description = models.CharField(max_length=255, blank=True, null=True)
    reference_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wallet_transactions'
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.transaction_type == self.TransactionType.CREDIT else '-'
        return f"{self.wallet.user.email}: {sign}₹{self.amount} ({self.source})"


class Coupon(models.Model):
    """Promotional Coupons. Supports percenrage and flat discounts, 
    minimum spend rules, expiration dates, glaobel / per-usage limits"""

    class DiscountType(models.TextChoices):
        PERCENTAGE = 'PERCENTAGE', 'Percentage (%)'
        FLAT = 'FLAT', 'Flat Amount (₹)'

    code = models.CharField(max_length=50, unique=True, db_index=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2)
    min_order_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    max_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Maximum discount cap for percentage discounts.")
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    usage_limit = models.PositiveIntegerField(default=100, help_text="Total times this coupon can be redeemed across all customers")
    per_usage_limit = models.PositiveIntegerField(default=1, help_text="Maximum times a single customer can redeem this coupon.")
    times_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'coupons'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} ({self.discount_type}: {self.discount_value})"

    def is_valid_for_cart(self, user, subtotal):
        """validates if coupon is active. within date window, meets min spend, and user limit."""
        now = timezone.now()
        if not self.is_active:
            return False, "this coupon is no longer active"
        if now < self.start_date:
            return False, "this coupon is not active yet"
        if now > self.end_date:
            return False, "This coupon has expired."
        if subtotal < self.min_order_amount:
            return False, f"Minimum spend of ₹{self.min_order_amount} required to use this coupon."
        if self.times_used >= self.usage_limit:
            return False, "This coupon has reached its maximum global usage limit."

        user_usages = self.usages.filter(user=user).count()
        if user_usages >= self.per_usage_limit:
            return False, f"You have already redeemed this coupon the maximum allowed times ({self.per_usage_limit})"

        return True, "Coupon is valid"

    def calculate_discount(self, subtotal):
        """Calculates discount amount respecting maximum discount caps."""
        if self.discount_type == self.DiscountType.PERCENTAGE:
            discount = (subtotal * (self.discount_value / Decimal('100.00'))).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
            return discount
        elif self.discount_type == self.DiscountType.FLAT:
            return min(self.discount_value, subtotal)
        return Decimal('0.00')


class CouponUsage(models.Model):
    """Audit log tracking each time a customer redeems a coupon on an Order."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='usages')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='coupon_usages')
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='coupon_usages', null=True, blank=True)
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'coupon_usages'
        ordering = ['-used_at']

    def __str__(self):
        return f"{self.user.email} used {self.coupon.code} (-₹{self.discount_applied})"