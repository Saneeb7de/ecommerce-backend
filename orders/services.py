from decimal import Decimal, ROUND_HALF_UP
from rest_framework.exceptions import ValidationError
from orders.models import Coupon


class PricingEngine:
    """
    Core Checkout Pricing & Tax Calculation Engine.
    Handles:
    - Line item calculations & live warehouse stock verification
    - Subtotal calculation
    - Tiered shipping fee (Free for orders >= ₹1,000, else ₹100)
    - Coupon discounts (e.g., WELCOME10 for 10% off)
    - 18% GST tax calculation on net taxable amount
    - Grand total calculation
    - Cash on Delivery (COD) limit enforcement (Max ₹50,000)
    """

    FREE_SHIPPING_THRESHOLD = Decimal('1000.00')
    STANDARD_SHIPPING_FEE = Decimal('100.00')
    TAX_RATE = Decimal('0.18')       # 18% GST
    COD_MAX_LIMIT = Decimal('50000.00')

    # Basic promotional coupon definitions
    SUPPORTED_COUPONS = {
        'WELCOME10': {
            'type': 'PERCENTAGE',
            'value': Decimal('10.00'),  # 10% off
            'min_spend': Decimal('500.00'),
        },
        'FLAT50': {
            'type': 'FLAT',
            'value': Decimal('50.00'),   # Flat ₹50 off
            'min_spend': Decimal('300.00'),
        }
    }

    @classmethod
    def calculate_cart_totals(cls, cart, coupon_code=None, user=None):
        """
        Takes an active Cart instance, verifies warehouse stock, and computes
        the complete authoritative financial breakdown for checkout.
        """
        # 1. Fetch line items with prefetching to avoid N+1 queries
        items = cart.items.select_related('variant__product', 'variant__inventory').all()
        if not items.exists():
            raise ValidationError("Your cart is empty. Add items before checking out.")

        subtotal = Decimal('0.00')
        line_breakdown = []

        # 2. Revalidate live stock & active status for every item
        for item in items:
            variant = item.variant
            if not variant or not variant.is_active or variant.product.is_deleted or not variant.product.is_published:
                raise ValidationError(f"Item '{variant.sku if variant else 'Unknown'}' is currently unavailable.")

            available_qty = variant.inventory.available_quantity if hasattr(variant, 'inventory') else 0
            if available_qty < item.quantity:
                raise ValidationError(
                    f"Insufficient stock for '{variant.sku}'. Requested: {item.quantity}, Available: {available_qty}."
                )

            unit_price = variant.effective_price
            line_total = (unit_price * item.quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            subtotal += line_total

            line_breakdown.append({
                'item_id': item.id,
                'variant_id': variant.id,
                'sku': variant.sku,
                'product_name': variant.product.name,
                'variant_name': variant.variant_name,
                'primary_image': variant.product.primary_image,
                'unit_price': unit_price,
                'quantity': item.quantity,
                'line_total': line_total,
            })

        # 3. Apply Coupon Discount (Database Coupon + Fallback)
        discount_amount = Decimal('0.00')
        applied_coupon = None
        if coupon_code:
            code = coupon_code.strip().upper()
            from orders.models import Coupon
            db_coupon = Coupon.objects.filter(code=code, is_active=True).first()
            if db_coupon:
                # 💡 Validate against database rules (min spend, date, per-user limits):
                is_valid, error_msg = db_coupon.is_valid_for_cart(user or cart.user, subtotal)
                if not is_valid:
                    raise ValidationError(error_msg)
                discount_amount = db_coupon.calculate_discount(subtotal)
                applied_coupon = db_coupon.code
            elif code in cls.SUPPORTED_COUPONS:
                coupon_data = cls.SUPPORTED_COUPONS[code]
                if subtotal >= coupon_data['min_spend']:
                    applied_coupon = code
                    if coupon_data['type'] == 'PERCENTAGE':
                        discount_amount = (subtotal * (coupon_data['value'] / Decimal('100.00'))).quantize(
                            Decimal('0.01'), rounding=ROUND_HALF_UP
                        )
                    elif coupon_data['type'] == 'FLAT':
                        discount_amount = min(coupon_data['value'], subtotal)
                else:
                    raise ValidationError(
                        f"Coupon '{code}' requires a minimum spend of ₹{coupon_data['min_spend']}."
                    )
            else:
                raise ValidationError(f"Invalid or expired coupon code '{coupon_code}'.")

        # 4. Calculate Tiered Shipping Fee
        if subtotal >= cls.FREE_SHIPPING_THRESHOLD:
            shipping_fee = Decimal('0.00')
        else:
            shipping_fee = cls.STANDARD_SHIPPING_FEE

        # 5. Calculate GST (18% on net taxable amount after discount)
        taxable_amount = max(Decimal('0.00'), subtotal - discount_amount)
        tax_amount = (taxable_amount * cls.TAX_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # 6. Calculate Final Grand Total
        grand_total = (subtotal - discount_amount + shipping_fee + tax_amount).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        # 7. Determine Cash on Delivery (COD) Eligibility
        is_cod_eligible = grand_total <= cls.COD_MAX_LIMIT

        return {
            'items': line_breakdown,
            'total_items': sum(i['quantity'] for i in line_breakdown),
            'subtotal': subtotal,
            'discount_amount': discount_amount,
            'applied_coupon': applied_coupon,
            'shipping_fee': shipping_fee,
            'tax_amount': tax_amount,
            'grand_total': grand_total,
            'is_cod_eligible': is_cod_eligible,
        }