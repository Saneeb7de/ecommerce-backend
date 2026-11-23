from django.contrib import admin
from .models import (
    Order,
    OrderItem,
    PaymentTransaction,
    OrderTrackingMilestone,
    ReturnRequest,
    Wallet,
    WalletTransaction,
    Coupon,
    CouponUsage,
)


class OrderItemInline(admin.TabularInline):
    """
    Renders frozen line items inside the Order view.
    Read-only to preserve historical financial integrity.
    """
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = (
        'sku_snapshot',
        'product_name_snapshot',
        'variant_name_snapshot',
        'unit_price',
        'quantity',
        'line_total',
    )
    fields = (
        'sku_snapshot',
        'product_name_snapshot',
        'variant_name_snapshot',
        'unit_price',
        'quantity',
        'line_total',
    )


class OrderTrackingMilestoneInline(admin.TabularInline):
    """Shows chronological delivery and status timeline."""
    model = OrderTrackingMilestone
    extra = 1
    readonly_fields = ('timestamp',)
    fields = ('status', 'notes', 'timestamp')


class PaymentTransactionInline(admin.TabularInline):
    """Audit log of all payment attempts (Razorpay, COD, Wallet)."""
    model = PaymentTransaction
    extra = 0
    can_delete = False
    readonly_fields = (
        'payment_method',
        'razorpay_order_id',
        'razorpay_payment_id',
        'amount',
        'currency',
        'status',
        'created_at',
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'user',
        'status',
        'payment_method',
        'payment_status',
        'grand_total',
        'created_at',
    )
    list_filter = ('status', 'payment_status', 'payment_method', 'created_at')
    search_fields = (
        'order_number',
        'user__email',
        'user__name',
        'tracking_number',
        'carrier_name',
    )
    readonly_fields = (
        'order_number',
        'user',
        'coupon',
        'subtotal',
        'discount_amount',
        'shipping_fee',
        'tax_amount',
        'grand_total',
        'shipping_address_snapshot',
        'created_at',
        'updated_at',
        'cancelled_at',
    )
    inlines = [OrderItemInline, OrderTrackingMilestoneInline, PaymentTransactionInline]
    ordering = ('-created_at',)

    fieldsets = (
        ("Order Header", {
            "fields": (
                "order_number",
                "user",
                "status",
                "payment_method",
                "payment_status",
            ),
        }),
        ("Financial Breakdown (Frozen)", {
            "fields": (
                "subtotal",
                "discount_amount",
                "coupon",
                "shipping_fee",
                "tax_amount",
                "grand_total",
            ),
        }),
        ("Delivery & Address Snapshot", {
            "fields": (
                "shipping_address_snapshot",
                "carrier_name",
                "tracking_number",
            ),
        }),
        ("Cancellation Details", {
            "fields": (
                "cancellation_reason",
                "cancelled_at",
            ),
        }),
        ("Timestamps", {
            "fields": (
                "created_at",
                "updated_at",
            ),
        }),
    )


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'order',
        'user',
        'return_type',
        'reason_type',
        'status',
        'created_at',
    )
    list_filter = ('status', 'return_type', 'reason_type', 'created_at')
    search_fields = ('order__order_number', 'user__email', 'reason')
    readonly_fields = ('order', 'user', 'created_at', 'reviewed_at')


class WalletTransactionInline(admin.TabularInline):
    """Immutable audit trail of credit/debit movements."""
    model = WalletTransaction
    extra = 0
    can_delete = False
    readonly_fields = (
        'amount',
        'transaction_type',
        'source',
        'description',
        'reference_id',
        'balance_after',
        'created_at',
    )


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'is_active', 'updated_at')
    search_fields = ('user__email', 'user__name')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [WalletTransactionInline]


class CouponUsageInline(admin.TabularInline):
    model = CouponUsage
    extra = 0
    can_delete = False
    readonly_fields = ('user', 'order', 'discount_applied', 'used_at')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'discount_type',
        'discount_value',
        'min_order_amount',
        'times_used',
        'usage_limit',
        'is_active',
        'end_date',
    )
    list_filter = ('discount_type', 'is_active', 'start_date', 'end_date')
    search_fields = ('code',)
    readonly_fields = ('times_used', 'created_at')
    inlines = [CouponUsageInline]