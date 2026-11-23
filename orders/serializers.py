from rest_framework import serializers
from decimal import ROUND_HALF_UP, Decimal
from django.utils import timezone
from django.db import models

from config import settings
from users.models import Address
from cart.models import Cart
from .models import Order, OrderItem, PaymentTransaction, OrderTrackingMilestone, ReturnRequest, Wallet, WalletTransaction, Coupon, CouponUsage
from .services import PricingEngine


class OrderItemSnapshotSerializer(serializers.ModelSerializer): #  Serializes frozen line item snapshots (tamper-proof historical records).
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product_name_snapshot', 'variant_name_snapshot',
            'sku_snapshot', 'primary_image_snapshot', 'unit_price', 'quantity', 'line_total'
        ]

class OrderTrackingMilestoneSerializer(serializers.ModelSerializer): # Serializes chronological tracking events (e.g. Confirmed -> Shipped).
    class Meta:
        model = OrderTrackingMilestone
        fields = ['id', 'status', 'notes', 'timestamp']


class PaymentTransactionSerializer(serializers.ModelSerializer): # Audit log for payment attempts (Razorpay, COD, Wallet).
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            'id', 'payment_method', 'razorpay_order_id', 'razorpay_payment_id', 'amount',
            'currency', 'status', 'error_code', 'error_description', 'created_at'
        ]


class ReturnRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnRequest
        exclude = ['order', 'user']
        read_only_fields = ['id', 'status', 'admin_notes', 'created_at', 'reviewed_at']


class OrderListSerializer(serializers.ModelSerializer): # Lightweight serializer for order history listing (Customer & Admin).
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    total_items = serializers.SerializerMethodField()
    customer_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_email', 'status', 'payment_method', 
            'payment_status','grand_total', 'total_items', 'created_at'
        ]

    def get_total_items(self, obj):
        return sum(item.quantity for item in obj.items.all())

class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSnapshotSerializer(many=True, read_only=True)
    milestones = OrderTrackingMilestoneSerializer(many=True, read_only=True)
    payments = PaymentTransactionSerializer(many=True, read_only=True)
    return_request = ReturnRequestSerializer(read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    shipping_fee = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    is_cancellable = serializers.BooleanField(read_only=True)
    is_returnable = serializers.BooleanField(read_only=True)
    customer_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_email', 'status',
            'payment_method', 'payment_status',
            'subtotal', 'discount_amount', 'shipping_fee', 'tax_amount', 'grand_total',
            'shipping_address_snapshot', 'tracking_number', 'carrier_name',
            'cancellation_reason', 'cancelled_at',
            'is_cancellable', 'is_returnable',
            'items', 'milestones', 'payments', 'return_request',
            'created_at', 'updated_at'
        ]


class CheckoutPreviewSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(required=False, allow_null=True)
    coupon_code = serializers.CharField(required=False, allow_blank=True, max_length=50)

    def validate_address_id(self, value):
        if value:
            user = self.context['request'].user
            if not Address.objects.filter(id=value, user=user).exists():
                raise serializers.ValidationError("Selected address does not exist or does not belong to you.")
        return value

class OrderCreateSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(required=True)
    payment_method = serializers.ChoiceField(choices=Order.PaymentMethod.choices, default=Order.PaymentMethod.COD)
    coupon_code = serializers.CharField(required=False, allow_blank=True, max_length=50)

    def validate_address_id(self, value):
        user = self.context['request'].user
        try:
            address = Address.objects.get(id=value, user=user)
        except Address.DoesNotExist:
            raise serializers.ValidationError("Selected address does not belongs to you.")
        return address

    def validate(self, attrs):
        user = self.context['request'].user
        cart = Cart.objects.prefetch_related('items__variant__product', 'items__variant__inventory').filter(user=user).first()
        if not cart or not cart.items.exists():
            raise serializers.ValidationError({"cart": "Your shopping cart is empty."})

        coupon_code = attrs.get('coupon_code', None)
        pricing = PricingEngine.calculate_cart_totals(cart, coupon_code=coupon_code, user=user)
        attrs['pricing'] = pricing
        attrs['cart'] = cart

        payment_method = attrs.get('payment_method')

        # COD Threshold Rule
        if payment_method == Order.PaymentMethod.COD and not pricing['is_cod_eligible']:
            raise serializers.ValidationError(
                {"payment_method": f"Cash on Delivery is unavailable for orders exceeding ₹{PricingEngine.COD_MAX_LIMIT}."}
            )
        # Store Wallet Balance Rule
        if payment_method == Order.PaymentMethod.WALLET:
            wallet, _ = Wallet.objects.get_or_create(user=user)
            if wallet.balance < pricing['grand_total']:
                raise serializers.ValidationError(
                    {"payment_method": f"Insufficient wallet balance. Available: ₹{wallet.balance}, Required: ₹{pricing['grand_total']}."}
                )
            attrs['wallet'] = wallet
        return attrs

class RazorpayPaymentVerifySerializer(serializers.Serializer): # Validates the 3 cryptographic tokens returned by the Razorpay client SDK.
    razorpay_order_id = serializers.CharField(required=True)
    razorpay_payment_id = serializers.CharField(required=True)
    razorpay_signature = serializers.CharField(required=True)

class OrderCancelSerializer(serializers.Serializer): 
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)

class ReturnRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnRequest
        fields = ['return_type', 'reason_type', 'reason']

    def validate_reason(self, value):
        if not value or len(value.strip()) < 10:
            raise serializers.ValidationError("Please provide a detailed explanation (minimum 10 characters).")

        return value.strip()


class AdminOrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.OrderStatus.choices)
    tracking_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    carrier_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=255)

class AdminReturnModerateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[ReturnRequest.ReturnStatus.APPROVED, ReturnRequest.ReturnStatus.REJECTED])
    admin_notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
    restock = serializers.BooleanField(required=False, default=None)

class WalletTransactionSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    balance_after = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = WalletTransaction
        fields = ['id', 'amount', 'transaction_type', 'source', 'description', 'reference_id', 'balance_after', 'created_at']

class WalletBalanceSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    class Meta:
        model = Wallet
        fields = ['balance', 'is_active', 'updated_at']

class WalletTopUpCreateOrderSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('10.00'))

class WalletTopUpVerifySerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField(required=True)
    razorpay_payment_id = serializers.CharField(required=True)
    razorpay_signature = serializers.CharField(required=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=Decimal('10.00'))

class CouponSerializer(serializers.ModelSerializer):
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    min_order_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True, required=False, default=Decimal('0.00'))
    max_discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True, required=False, allow_null=True)

    class Meta:
        model = Coupon
        fields = '__all__'
        read_only_fields = ['id', 'times_used', 'created_at']

    def validate_code(self, value):
        cleaned = value.strip().upper()
        if not cleaned:
            raise serializers.ValidationError("Coupon code cannot be empty.")

        queryset = Coupon.objects.filter(code=cleaned)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(f"Coupon code '{cleaned}' already exists.")

        return cleaned

    def validate(self, attrs):
        start_date = attrs.get('start_date') or (self.instance.start_date if self.instance else timezone.now())
        end_date = attrs.get('end_date') or (self.instance.end_date if self.instance else None)

        if end_date and start_date and end_date <= start_date:
            raise serializers.ValidationError({"end_date": "Coupon end date must be strictly after the start date."})

        discount_type = attrs.get('discount_type') or (self.instance.discount_type if self.instance else Coupon.DiscountType.PERCENTAGE)
        discount_value = attrs.get('discount_value') or (self.instance.discount_value if self.instance else None)

        if discount_value is not None:
            if discount_value <= Decimal('0.00'):
                raise serializers.ValidationError({"discount_value": "Discount value must be greater than zero."})
            if discount_type == Coupon.DiscountType.PERCENTAGE and discount_value > Decimal('100.00'):
                raise serializers.ValidationError({"discount_value": "Percentage discount cannot exceed 100%."})

        usage_limit = attrs.get('usage_limit', getattr(self.instance, 'usage_limit', 100))
        per_usage_limit = attrs.get('per_usage_limit', getattr(self.instance, 'per_usage_limit', 1))

        if per_usage_limit > usage_limit:
            raise serializers.ValidationError({"per_usage_limit": "Per-user usage limit cannot exceed the global usage limit."})

        return attrs


class CouponUsageSerializer(serializers.ModelSerializer):
    customer_email = serializers.EmailField(source='user.email', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True, default=None)
    discount_applied = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = CouponUsage
        fields = ['id', 'customer_email', 'order_number', 'discount_applied', 'used_at']


class AdminCouponDetailSerializer(serializers.ModelSerializer):
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    min_order_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    max_discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True, allow_null=True)
    total_discount_given = serializers.SerializerMethodField()
    recent_usages = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = '__all__'

    def get_total_discount_given(self, obj):
        total = obj.usages.aggregate(total=models.Sum('discount_applied'))['total']
        return str(total or Decimal('0.00'))

    def get_recent_usages(self, obj):
        recent = obj.usages.select_related('user', 'order').order_by('-used_at')[:10]
        return CouponUsageSerializer(recent, many=True).data


class CustomerCouponSerializer(serializers.ModelSerializer):
    """
    Sanitized public view for Shoppers with personalized eligibility checks.
    """
    # Only dynamic, per-user fields need explicit definition:
    user_usage_count = serializers.SerializerMethodField()
    is_eligible = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'discount_type', 'discount_value',
            'min_order_amount', 'max_discount_amount',
            'start_date', 'end_date', 'per_usage_limit',
            'user_usage_count', 'is_eligible'
        ]

    def get_user_usage_count(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            if not hasattr(obj, '_cached_user_usage'):
                obj._cached_user_usage = obj.usages.filter(user=request.user).count()
            return obj._cached_user_usage
        return 0

    def get_is_eligible(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            usage_count = self.get_user_usage_count(obj)
            return usage_count < obj.per_usage_limit
        return True


class AdminReturnRequestListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for Admin Return Queue."""
    order_id = serializers.IntegerField(source='order.id', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    customer_email = serializers.EmailField(source='user.email', read_only=True)
    customer_name = serializers.CharField(source='user.name', read_only=True)
    order_grand_total = serializers.DecimalField(source='order.grand_total', max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True)
    return_type_display = serializers.CharField(source='get_return_type_display', read_only=True)
    reason_type_display = serializers.CharField(source='get_reason_type_display', read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            'id', 'order_id', 'order_number', 'customer_email', 'customer_name',
            'order_grand_total', 'return_type', 'return_type_display',
            'reason_type', 'reason_type_display', 'status', 'created_at', 'reviewed_at'
        ]


class AdminReturnRequestDetailSerializer(serializers.ModelSerializer):
    """Comprehensive serializer for Admin to inspect return claims with full order context."""
    order_id = serializers.IntegerField(source='order.id', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    customer_email = serializers.EmailField(source='user.email', read_only=True)
    customer_name = serializers.CharField(source='user.name', read_only=True)
    recipient_phone = serializers.SerializerMethodField()
    order = OrderDetailSerializer(read_only=True)
    return_type_display = serializers.CharField(source='get_return_type_display', read_only=True)
    reason_type_display = serializers.CharField(source='get_reason_type_display', read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            'id', 'order_id', 'order_number', 'customer_email', 'customer_name', 'recipient_phone',
            'return_type', 'return_type_display', 'reason_type', 'reason_type_display',
            'reason', 'status', 'admin_notes', 'created_at', 'reviewed_at', 'order'
        ]

    def get_recipient_phone(self, obj):
        # Safely extracts phone_number from the frozen shipping address snapshot
        if obj.order and isinstance(obj.order.shipping_address_snapshot, dict):
            return obj.order.shipping_address_snapshot.get('phone_number', '')
        return ''

class AdminWalletTransactionSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='wallet.user.id', read_only=True)
    customer_email = serializers.EmailField(source='wallet.user.email', read_only=True)
    customer_name = serializers.CharField(source='wallet.user.name', read_only=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    balance_after = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = WalletTransaction
        fields = [
            'id', 'user_id', 'customer_email', 'customer_name', 'amount', 'transaction_type',
            'source', 'description', 'reference_id', 'balance_after', 'created_at' 
        ]


class OrderInvoiceSerializer(serializers.ModelSerializer):
    invoice_number = serializers.SerializerMethodField()
    seller_details = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source='user.name', read_only=True)
    customer_email = serializers.EmailField(source='user.email', read_only=True)
    tax_breakdown = serializers.SerializerMethodField()
    items = OrderItemSnapshotSerializer(many=True, read_only=True)
    payment_details = serializers.SerializerMethodField()

    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    shipping_fee = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = Order
        fields = [
            'invoice_number', 'order_number', 'created_at',
            'seller_details', 'customer_name', 'customer_email',
            'shipping_address_snapshot', 'payment_details',
            'items', 'subtotal', 'discount_amount', 'shipping_fee',
            'tax_amount', 'tax_breakdown', 'grand_total'
        ]

    def get_invoice_number(self, obj):
        return f"INV-{obj.order_number.replace('ORD-', '')}"

    def get_seller_details(self, obj):
        # Single-store official merchant credentials for the invoice header
        return {
            "store_name": getattr(settings, 'STORE_NAME', 'E-Commerce Store Pvt Ltd'),
            "gstin": getattr(settings, 'STORE_GSTIN', '29AAAAA0000A1Z5'),
            "registered_address": getattr(settings, 'STORE_ADDRESS', 'Bengaluru, Karnataka, India - 560100'),
            "support_email": getattr(settings, 'STORE_SUPPORT_EMAIL', 'support@ecommerce.com')
        }

    def get_tax_breakdown(self, obj):
        # 18% GST split into 9% CGST + 9% SGST
        half_tax = (obj.tax_amount / Decimal('2.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return {
            "gst_rate": "18%",
            "cgst_rate": "9%",
            "cgst_amount": str(half_tax),
            "sgst_rate": "9%",
            "sgst_amount": str(half_tax),
            "total_tax": str(obj.tax_amount)
        }

    def get_payment_details(self, obj):
        successful_payment = obj.payments.filter(status=PaymentTransaction.TransactionStatus.SUCCESS).first()
        return {
            "payment_method": obj.payment_method,
            "payment_status": obj.payment_status,
            "transaction_reference": successful_payment.razorpay_payment_id if successful_payment else "N/A"
        }

    