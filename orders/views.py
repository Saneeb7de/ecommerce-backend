import razorpay
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from django.db import models
from rest_framework.permissions import AllowAny, IsAuthenticated
from datetime import timedelta
from django.db.models.functions import TruncDate, TruncMonth
from django.core.cache import cache
from rest_framework.throttling import ScopedRateThrottle

from orders.utils import invalidate_dashboard_cache
from users.models import User
from users.permissions import IsAdmin, IsCustomer
from catalog.models import Inventory, InventoryTransaction
from catalog.utils import release_redis_stock
from cart.models import Cart
from .models import Order, OrderItem, PaymentTransaction, OrderTrackingMilestone, ReturnRequest, Wallet, WalletTransaction, Coupon, CouponUsage
from .serializers import (
    AdminCouponDetailSerializer, AdminReturnRequestDetailSerializer, AdminReturnRequestListSerializer, AdminWalletTransactionSerializer, CheckoutPreviewSerializer, CustomerCouponSerializer, OrderCreateSerializer, OrderDetailSerializer, OrderInvoiceSerializer, OrderListSerializer, OrderCancelSerializer,
    OrderTrackingMilestoneSerializer, ReturnRequestCreateSerializer, ReturnRequestSerializer,
    RazorpayPaymentVerifySerializer, AdminOrderStatusUpdateSerializer, AdminReturnModerateSerializer,
    WalletBalanceSerializer, WalletTopUpCreateOrderSerializer, WalletTransactionSerializer, WalletTopUpVerifySerializer,
    CouponSerializer
)
from .services import PricingEngine
from users.pagination import StandardResultsSetPagination
from notifications.utils import send_dynamic_email


  

  

  

# Initialize Razorpay Client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

class CheckoutPreviewView(APIView):
    permission_classes = [IsCustomer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'coupon_apply'

    def post(self, request):
        serializer = CheckoutPreviewSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        cart = Cart.objects.prefetch_related('items__variant__product', 'items__variant__inventory').filter(user=request.user).first()
        if not cart or not cart.items.exists():
            return Response({"success": False, "message": "Your cart is empty."}, status=status.HTTP_400_BAD_REQUEST)

        coupon_code = serializer.validated_data.get('coupon_code', None)
        try:
            pricing = PricingEngine.calculate_cart_totals(cart, coupon_code=coupon_code, user=request.user)
        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "data": pricing
            },
            status=status.HTTP_200_OK
        )


class OrderCreateListView(APIView):
    permission_classes = [IsCustomer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'checkout'

    def get(self, request):
        orders = Order.objects.filter(user=request.user).select_related('user').prefetch_related('items').order_by('-created_at')
        serializer = OrderListSerializer(orders, many=True)
        return Response(
            {
                "success": True,
                "count": orders.count(),
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        address = serializer.validated_data['address_id']
        payment_method = serializer.validated_data['payment_method']
        pricing = serializer.validated_data['pricing']
        cart = serializer.validated_data['cart']

        with transaction.atomic():
            # Lock warehouse inventory rows using pessimistic locking
            variant_ids = [item['variant_id'] for item in pricing['items']]
            locked_inventories = {
                inv.variant_id: inv for inv in Inventory.objects.select_for_update().filter(variant_id__in=variant_ids)
            }

            #  re-verify available stock under pessimistic lock
            for item in pricing['items']:
                inv = locked_inventories.get(item['variant_id'])
                if not inv or inv.available_quantity < item['quantity']:
                    return Response(
                        {"success": False, "message": f"Item '{item['sku']}' just sold out. Please review your cart."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            address_snapshot = {
                "recipient_name": address.recipient_name,
                "phone_number": address.phone_number,
                "street_address": address.street_address,
                "apartment_suite": address.apartment_suite or "",
                "city": address.city,
                "state": address.state,
                "postal_code": address.postal_code,
                "country": address.country,
            }

            coupon_code = serializer.validated_data.get('coupon_code', None)
            coupon_obj = None

            if coupon_code:
                coupon_obj = Coupon.objects.filter(code__iexact=coupon_code, is_active=True).first()

            order = Order.objects.create(
                user=request.user,
                shipping_address_snapshot=address_snapshot,
                payment_method=payment_method,
                coupon=coupon_obj,
                subtotal=pricing['subtotal'],
                discount_amount=pricing['discount_amount'],
                shipping_fee=pricing['shipping_fee'],
                tax_amount=pricing['tax_amount'],
                grand_total=pricing['grand_total'],
                status=Order.OrderStatus.PENDING,
                payment_status=Order.PaymentStatus.PENDING,
            )

            #  frozen OrderItem snapshots + deduct inventory
            for item in pricing['items']:
                inv = locked_inventories[item['variant_id']]

                OrderItem.objects.create(
                    order=order,
                    variant_id=item['variant_id'],
                    product_name_snapshot=item['product_name'],
                    variant_name_snapshot=item['variant_name'],
                    sku_snapshot=item['sku'],
                    primary_image_snapshot=item['primary_image'],
                    unit_price=item['unit_price'],
                    quantity=item['quantity'],
                    line_total=item['line_total'],
                )

                inv.quantity -= item['quantity']
                inv.save(update_fields=['quantity', 'updated_at'])

                InventoryTransaction.objects.create(
                    inventory=inv,
                    transaction_type=InventoryTransaction.TransactionType.ORDER_RESERVED,
                    quantity=-item['quantity'],
                    balance_after=inv.quantity,
                    reference_id=order.order_number,
                    description=f"Stock reserver for order {order.order_number}",
                )

            # Mark coupon as used
            if coupon_obj:
                CouponUsage.objects.create(user=request.user, coupon=coupon_obj, order=order, discount_applied=pricing['discount_amount'])
                Coupon.objects.filter(id=coupon_obj.id).update(times_used=models.F('times_used') + 1)


            cart.items.all().delete()

            OrderTrackingMilestone.objects.create(
                order=order,
                status=Order.OrderStatus.PENDING,
                notes="Order placed successfully."
            )

        # payment dealing
        if payment_method == Order.PaymentMethod.COD:
            order.payment_status = Order.PaymentStatus.PENDING
            order.save(update_fields=['payment_status'])
            send_dynamic_email(
                recipient_email=order.user.email,
                template_key='ORDER_CONFIRMATION',
                fallback_file='order_confirmation.html',
                default_subject=f"Order Confirmed: #{order.order_number}",
                context_dict={
                    'order': order,
                    'customer_name': getattr(order.user, 'name', None) or order.user.email,
                    'items': order.items.all(),
                    'shipping_address': order.shipping_address_snapshot or {}
                }
            )
            invalidate_dashboard_cache()
            return Response(
                {"success": True, "message": "Order placed successfully.", "data": {"id": order.id, "order_number": order.order_number}},
                status=status.HTTP_201_CREATED
            )

        elif payment_method == Order.PaymentMethod.WALLET:
            wallet = serializer.validated_data['wallet']
            with transaction.atomic():
                wallet.debit(
                    amount=pricing['grand_total'],
                    source=WalletTransaction.TransactionSource.ORDER_PAYMENT,
                    reference_id=order.order_number,
                    description=f"Payment for order {order.order_number}"
                )
                order.payment_status = Order.PaymentStatus.COMPLETED
                order.status = Order.OrderStatus.CONFIRMED
                order.save(update_fields=['payment_status', 'status'])
                send_dynamic_email(
                    recipient_email=order.user.email,
                    template_key='ORDER_CONFIRMATION',
                    fallback_file='order_confirmation.html',
                    default_subject=f"Order Confirmed: #{order.order_number}",
                    context_dict={
                        'order': order,
                        'customer_name': getattr(order.user, 'name', None) or order.user.email,
                        'items': order.items.all(),
                        'shipping_address': order.shipping_address_snapshot or {}
                    }
                )

                PaymentTransaction.objects.create(
                    order=order,
                    payment_method=Order.PaymentMethod.WALLET,
                    amount=pricing['grand_total'],
                    status=PaymentTransaction.TransactionStatus.SUCCESS,
                )

                OrderTrackingMilestone.objects.create(
                    order=order,
                    status=Order.OrderStatus.CONFIRMED,
                    notes="Payment confirmed via Wallet."
                )

            invalidate_dashboard_cache()

            return Response(
                {"success": True, "message": "Order placed and paid via Wallet", "data": {"id": order.id, "order_number": order.order_number}},
                status=status.HTTP_201_CREATED
            )

        elif payment_method == Order.PaymentMethod.RAZORPAY:
            razorpay_order = razorpay_client.order.create({
                "amount": int(pricing['grand_total'] * 100),
                "currency": "INR",
                "receipt": order.order_number,
                "payment_capture": 1
            })
            PaymentTransaction.objects.create(
                order=order,
                payment_method=Order.PaymentMethod.RAZORPAY,
                razorpay_order_id=razorpay_order['id'],
                amount=pricing['grand_total'],
                status=PaymentTransaction.TransactionStatus.INITIATED,
            )

            return Response(
                {
                    "success": True,
                    "message": "Razorpay order created. Complete payment on the client.",
                    "data": {
                        "id": order.id,
                        "order_number": order.order_number,
                        "razorpay_order_id": razorpay_order['id'],
                        "amount": pricing['grand_total'],
                        "currency": "INR",
                        "razorpay_key": settings.RAZORPAY_KEY_ID,
                    }
                },
                status=status.HTTP_201_CREATED
            )


class OrderDetailView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request, identifier):
        queryset = Order.objects.filter(user=request.user).select_related('user').prefetch_related('items', 'milestones', 'payments')

        # Dual lookup- numeric ID or order_number
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        serializer = OrderDetailSerializer(order)
        return Response(
            {
                "success": True,
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )


class OrderCancelView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request, identifier):
        serializer = OrderCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "error": serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = Order.objects.filter(user=request.user)
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        if not order.is_cancellable:
            return Response(
                {
                    "success": False,
                    "message": f"Order cannot be cancelled in its current state '{order.status}'. Cancellations are only permitted before shipment."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        reason = serializer.validated_data.get('reason', '')

        with transaction.atomic():
            order.status = Order.OrderStatus.CANCELLED
            order.cancellation_reason = reason
            order.cancelled_at = timezone.now()

            refund_issued = False
            if order.payment_status == Order.PaymentStatus.COMPLETED:
                wallet, _ = Wallet.objects.get_or_create(user=request.user)
                wallet.credit(
                    amount=order.grand_total,
                    source=WalletTransaction.TransactionSource.ORDER_REFUND,
                    reference_id=order.order_number,
                    description=f"Automated refund for cancelled order {order.order_number}"
                )
                order.payment_status = Order.PaymentStatus.REFUNDED
                refund_issued = True
            elif order.payment_status == Order.PaymentStatus.PENDING:
                order.payment_status = Order.PaymentStatus.FAILED

            order.save(update_fields=['status', 'cancellation_reason', 'cancelled_at', 'payment_status', 'updated_at'])

            line_items = order.items.select_related('variant__inventory').all()
            for item in line_items:
                if item.variant and hasattr(item.variant, 'inventory'):
                    inv = Inventory.objects.select_for_update().get(id=item.variant.inventory.id)
                    inv.quantity += item.quantity
                    inv.save(update_fields=['quantity', 'updated_at'])

                    InventoryTransaction.objects.create(
                        inventory=inv,
                        transaction_type=InventoryTransaction.TransactionType.ORDER_CANCELLED,
                        quantity=item.quantity,
                        balance_after=inv.quantity,
                        reference_id=order.order_number,
                        description=f"Stock restored: Order {order.order_number} cancelled"
                    )

                    release_redis_stock(sku=item.sku_snapshot, quantity=item.quantity)

            if order.coupon:
                CouponUsage.objects.filter(order=order, user=order.user).delete()
                Coupon.objects.filter(id=order.coupon_id, times_used__gt=0).update(times_used=models.F('times_used') - 1)

            OrderTrackingMilestone.objects.create(
                order=order,
                status=Order.OrderStatus.CANCELLED,
                notes=f"Order cancelled by customer. Reason: {reason or 'Not specified'}"
            )

            invalidate_dashboard_cache()

        send_dynamic_email(
            recipient_email=order.user.email,
            template_key='ORDER_CANCELLED',
            fallback_file='order_status_update.html',
            default_subject=f"Order Cancelled: #{order.order_number}",
            context_dict={
                'order': order,
                'customer_name': getattr(order.user, 'name', None) or order.user.email,
                'headline': 'Order Cancelled'
            }
        )

        return Response(
            {
                "success": True,
                "message": "Order cancelled successfully.",
                "data": {
                    "order_number": order.order_number,
                    "status": order.status,
                    "refund_issued": refund_issued,
                    "refund_amount": str(order.grand_total) if refund_issued else "0.00"
                }
            },
            status=status.HTTP_200_OK
        )


class OrderTrackingView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request, identifier):
        queryset = Order.objects.filter(user=request.user).prefetch_related('milestones')
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        milestones_serializer = OrderTrackingMilestoneSerializer(order.milestones.all(), many=True)
        return Response(
            {
                "success": True,
                "data": {
                    "order_number": order.order_number,
                    "status": order.status,
                    "carrier_name": order.carrier_name,
                    "tracking_number": order.tracking_number,
                    "milestones": milestones_serializer.data,
                }
            },
            status=status.HTTP_200_OK
        )


class OrderReturnRequestView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request, identifier):
        queryset = Order.objects.filter(user=request.user)
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        if order.status != Order.OrderStatus.DELIVERED:
            return Response(
                {
                    "success": False,
                    "message": f"Returns are only permitted for delivered orders. Current status: '{order.status}'."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if not order.is_returnable:
            return Response(
                {
                    "success": False,
                    "message": "The 7-day return policy window for this order has expired."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if hasattr(order, 'return_request'):
            return Response(
                {
                    "success": False,
                    "message": "A return request has already been submitted for this order."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ReturnRequestCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            return_request = serializer.save(order=order, user=request.user)

            order.status = Order.OrderStatus.RETURN_REQUESTED
            order.save(update_fields=['status', 'updated_at'])

            OrderTrackingMilestone.objects.create(
                order=order,
                status=Order.OrderStatus.RETURN_REQUESTED,
                notes=f"Return claim submitted ({return_request.get_reason_type_display()}). Awaiting review."
            )

        return Response(
            {
                "success": True,
                "message": "Return request submitted successfully. Our team will review your claim.",
                "data": ReturnRequestSerializer(return_request).data
            },
            status=status.HTTP_201_CREATED
        )

class RazorpayVerifyPaymentView(APIView):
    permission_classes = [IsCustomer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'payment_verify'

    def post(self, request, identifier):
        serializer = RazorpayPaymentVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "error": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = Order.objects.filter(user=request.user)
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        if order.payment_method != Order.PaymentMethod.RAZORPAY:
            return Response(
                {"success": False, "message": "This order does not use Razorpay payment."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if order.payment_status == Order.PaymentStatus.COMPLETED:
            return Response(
                {"success": False, "message": "Payment already verified for this order."},
                status=status.HTTP_400_BAD_REQUEST
            )

        razorpay_order_id = serializer.validated_data['razorpay_order_id']
        razorpay_payment_id = serializer.validated_data['razorpay_payment_id']
        razorpay_signature = serializer.validated_data['razorpay_signature']

        try:
            razorpay_client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature,
            })
        except razorpay.errors.SignatureVerificationError:
            # Record the failed attempt for audit trail
            PaymentTransaction.objects.create(
                order=order,
                payment_method=Order.PaymentMethod.RAZORPAY,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
                amount=order.grand_total,
                status=PaymentTransaction.TransactionStatus.FAILED,
            )
            return Response(
                {"success": False, "message": "Payment verification failed. Invalid signature."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            order.payment_status = Order.PaymentStatus.COMPLETED
            order.status = Order.OrderStatus.CONFIRMED
            order.save(update_fields=['payment_status', 'status', 'updated_at'])

            PaymentTransaction.objects.filter(
                order=order,
                razorpay_order_id=razorpay_order_id,
                status=PaymentTransaction.TransactionStatus.INITIATED
            ).update(
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
                status=PaymentTransaction.TransactionStatus.SUCCESS
            )

            OrderTrackingMilestone.objects.create(
                order=order,
                status=Order.OrderStatus.CONFIRMED,
                notes="Payment confirmed via Razorpay."
            )

            invalidate_dashboard_cache()

            send_dynamic_email(
                recipient_email=order.user.email,
                template_key='ORDER_CONFIRMATION',
                fallback_file='order_confirmation.html',
                default_subject=f"Order Confirmed: #{order.order_number}",
                context_dict={
                    'order': order,
                    'customer_name': getattr(order.user, 'name', None) or order.user.email,
                    'items': order.items.all(),
                    'shipping_address': order.shipping_address_snapshot or {}
                }
            )

            return Response(
            {
                "success": True,
                "message": "Payment verified. Order confirmed.",
                "data": {
                    "order_number": order.order_number,
                    "status": order.status,
                    "payment_status": order.payment_status,
                    "razorpay_payment_id": razorpay_payment_id,
                }
            },
            status=status.HTTP_200_OK
        )

class RazorpayRetryPaymentView(APIView):
    permission_classes = [IsCustomer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'payment_verify'
    
    def post(self, request, identifier):
        queryset = Order.objects.filter(user=request.user)
        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)

        if order.payment_method != Order.PaymentMethod.RAZORPAY:
            return Response(
                {"success": False, "message": "Retry is only applicable for Razorpay payment orders."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if order.payment_status != Order.PaymentStatus.PENDING:
            return Response(
                {"success": False, "message": f"Cannot retry payment. Current payment status is '{order.payment_status}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if order.status not in [Order.OrderStatus.PENDING]:
            return Response(
                {
                    "success": False,
                    "message": f"Order is not in a retryable state. Current status: '{order.status}'."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            razorpay_order = razorpay_client.order.create({
                "amount": int(order.grand_total * 100),  # paise
                "currency": "INR",
                "receipt": order.order_number,
                "payment_capture": 1
            })
        except Exception as e:
            return Response(
                {"success": False, "message": f"Failed to create Razorpay retry order: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        PaymentTransaction.objects.create(
            order=order,
            payment_method=Order.PaymentMethod.RAZORPAY,
            razorpay_order_id=razorpay_order['id'],
            amount=order.grand_total,
            status=PaymentTransaction.TransactionStatus.INITIATED,
        )

        return Response(
            {
                "success": True,
                "message": "New payment session created. Proceed with payment.",
                "data": {
                    "order_number": order.order_number,
                    "razorpay_order_id": razorpay_order['id'],
                    "amount": order.grand_total,
                    "currency": "INR",
                    "razorpay_key": settings.RAZORPAY_KEY_ID,
                }
            },
            status=status.HTTP_200_OK
        )
    

class AdminOrderListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = Order.objects.select_related('user').prefetch_related('items').order_by('-created_at')

        status_filter = request.query_params.get('status', None)
        if status_filter:
            status_filter_upper = status_filter.upper()
            valid_statuses = [s[0] for s in Order.OrderStatus.choices]
            if status_filter_upper not in valid_statuses:
                return Response(
                    {
                        "success": False,
                        "message": f"Invalid status '{status_filter}'. Valid values: {valid_statuses}"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            queryset = queryset.filter(status=status_filter_upper)

        payment_method_filter = request.query_params.get('payment_method', None)
        if payment_method_filter:
            queryset = queryset.filter(payment_method=payment_method_filter.upper())

        paginator = StandardResultsSetPagination()
        paginated_orders = paginator.paginate_queryset(queryset, request)
        serializer = OrderListSerializer(paginated_orders, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminOrderDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        order = get_object_or_404(Order.objects.select_related('user').prefetch_related('items', 'milestones', 'payments', 'return_request'), id=pk)
        serializer = OrderDetailSerializer(order)
        return Response(
            {
                "success": True,
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )


class AdminOrderStatusUpdateView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        order = get_object_or_404(Order.objects.select_related('user'), id=pk)
        serializer = AdminOrderStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response( {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        new_status = serializer.validated_data['status']
        tracking_number = serializer.validated_data.get('tracking_number', None)
        carrier_name = serializer.validated_data.get('carrier_name', None)
        notes = serializer.validated_data.get('notes', '')

        if not order.can_transition_to(new_status):
            return Response(
                {
                    "success": False,
                    "message": f"Cannot transition order from '{order.status}' to '{new_status}'.",
                    "current_status": order.status,
                    "requested_status": new_status
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        updated_fields = ['status', 'updated_at']
        order.status = new_status

        if new_status == Order.OrderStatus.SHIPPED:
            if not carrier_name or not tracking_number:
                return Response(
                    {
                        "success": False,
                        "message": "carrier_name and tracking_number are required when marking an order as SHIPPED."
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            order.carrier_name = carrier_name
            order.tracking_number = tracking_number
            updated_fields.extend(['carrier_name', 'tracking_number'])

        with transaction.atomic():
            order.save(update_fields=updated_fields)

            if new_status == Order.OrderStatus.DELIVERED:
                for item in order.items.select_related('variant__inventory').all():
                    if item.variant and hasattr(item.variant, 'inventory'):
                        inv = item.variant.inventory
                        InventoryTransaction.objects.create(
                            inventory=inv,
                            transaction_type=InventoryTransaction.TransactionType.ORDER_FULFILLED,
                            quantity=0,
                            balance_after=inv.quantity,
                            reference_id=order.order_number,
                            description=f"Order {order.order_number} delivered to customer."
                        )

            if new_status == Order.OrderStatus.DELIVERED and order.payment_method == Order.PaymentMethod.COD:
                order.payment_status = Order.PaymentStatus.COMPLETED
                order.save(update_fields=['payment_status'])
                PaymentTransaction.objects.create(
                    order=order,
                    payment_method=Order.PaymentMethod.COD,
                    amount=order.grand_total,
                    status=PaymentTransaction.TransactionStatus.SUCCESS
                )

            if new_status == Order.OrderStatus.CANCELLED:
                if order.payment_status == Order.PaymentStatus.COMPLETED:
                    wallet, _ = Wallet.objects.get_or_create(user=order.user)
                    wallet.credit(
                        amount=order.grand_total,
                        source=WalletTransaction.TransactionSource.ORDER_REFUND,
                        reference_id=order.order_number,
                        description=f"Automated refund for order {order.order_number} cancelled by admin"
                    )

                    order.payment_status = Order.PaymentStatus.REFUNDED
                elif order.payment_status == Order.PaymentStatus.PENDING:
                    order.payment_status = Order.PaymentStatus.FAILED

                order.cancellation_reason = notes or "Cancelled by administrator"
                order.cancelled_at = timezone.now()
                order.save(update_fields=['payment_status', 'cancellation_reason', 'cancelled_at'])

                for item in order.items.select_related('variant__inventory').all():
                    if item.variant and hasattr(item.variant, 'inventory'):
                        inv = Inventory.objects.select_for_update().get(id=item.variant.inventory.id)
                        inv.quantity += item.quantity
                        inv.save(update_fields=['quantity', 'updated_at'])

                        InventoryTransaction.objects.create(
                            inventory=inv,
                            transaction_type=InventoryTransaction.TransactionType.ORDER_CANCELLED,
                            quantity=item.quantity,
                            balance_after=inv.quantity,
                            reference_id=order.order_number,
                            description=f"Stock restored: Order {order.order_number} cancelled by admin"
                        )

                        release_redis_stock(sku=item.sku_snapshot, quantity=item.quantity)
                if order.coupon:
                    CouponUsage.objects.filter(order=order, user=order.user).delete()
                    Coupon.objects.filter(id=order.coupon_id, times_used__gt=0).update(times_used=models.F('times_used') - 1)

            milestone_notes = notes or f"Order status updated to {new_status} by admin."
            OrderTrackingMilestone.objects.create(
                order=order,
                status=new_status,
                notes=milestone_notes
            )

            if new_status == Order.OrderStatus.SHIPPED:
                send_dynamic_email(
                    recipient_email=order.user.email,
                    template_key='ORDER_SHIPPED',
                    fallback_file='order_status_update.html',
                    default_subject=f"Your Order #{order.order_number} has been Shipped!",
                    context_dict={
                        'order': order,
                        'customer_name': getattr(order.user, 'name', None) or order.user.email,
                        'headline': 'Order Shipped! 🚀'
                    }
                )
            elif new_status == Order.OrderStatus.DELIVERED:
                send_dynamic_email(
                    recipient_email=order.user.email,
                    template_key='ORDER_DELIVERED',
                    fallback_file='order_status_update.html',
                    default_subject=f"Delivered: Order #{order.order_number}",
                    context_dict={
                        'order': order,
                        'customer_name': getattr(order.user, 'name', None) or order.user.email,
                        'headline': 'Order Delivered 🎉'
                    }
                )

            elif new_status == Order.OrderStatus.CANCELLED:
                send_dynamic_email(
                    recipient_email=order.user.email,
                    template_key='ORDER_CANCELLED',
                    fallback_file='order_status_update.html',
                    default_subject=f"Order Cancelled: #{order.order_number}",
                    context_dict={
                        'order': order,
                        'customer_name': getattr(order.user, 'name', None) or order.user.email,
                        'headline': 'Order Cancelled by Store'
                    }
                )
            
            invalidate_dashboard_cache()

            return Response(
                {
                    "success": True,
                    "message": f"Order status updated to '{new_status}'.",
                    "data": {
                        "order_number": order.order_number,
                        "status": order.status,
                        "carrier_name": order.carrier_name,
                        "tracking_number": order.tracking_number,
                    }
                },
                status=status.HTTP_200_OK
            )


class AdminReturnModerateView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        return_request = get_object_or_404(
            ReturnRequest.objects.select_related(
                'order__user', 'order__coupon', 'user'
            ).prefetch_related('order__items__variant__inventory'),
            id=pk
        )

        serializer = AdminReturnModerateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        if return_request.status != ReturnRequest.ReturnStatus.PENDING:
            return Response(
                {
                    "success": False,
                    "message": f"This return request has already been '{return_request.status}'. Cannot moderate again."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        new_status = serializer.validated_data['status']
        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_restock_override = serializer.validated_data.get('restock', None)

        order = return_request.order

        with transaction.atomic():
            return_request.status = new_status
            return_request.admin_notes = admin_notes
            return_request.reviewed_at = timezone.now()
            return_request.save(update_fields=['status', 'admin_notes', 'reviewed_at'])

            items_restocked = False

            if new_status == ReturnRequest.ReturnStatus.APPROVED:

                if return_request.return_type == ReturnRequest.ReturnType.REFUND:
                    wallet, _ = Wallet.objects.get_or_create(user=order.user)
                    wallet.credit(
                        amount=order.grand_total,
                        source=WalletTransaction.TransactionSource.ORDER_REFUND,
                        reference_id=order.order_number,
                        description=f"Refund approved for return on order {order.order_number}"
                    )
                    order.payment_status = Order.PaymentStatus.REFUNDED
                    order.save(update_fields=['payment_status'])

                unsellable_reasons = [ReturnRequest.ReturnReason.DAMAGED, ReturnRequest.ReturnReason.DEFECTIVE]

                if admin_restock_override is not None:
                    should_restock = admin_restock_override
                else:
                    should_restock = return_request.reason_type not in unsellable_reasons

                if should_restock:
                    for item in order.items.select_related('variant__inventory').all():
                        if item.variant and hasattr(item.variant, 'inventory'):
                            inv = Inventory.objects.select_for_update().get(id=item.variant.inventory.id)
                            inv.quantity += item.quantity
                            inv.save(update_fields=['quantity', 'updated_at'])

                            InventoryTransaction.objects.create(
                                inventory=inv,
                                transaction_type=InventoryTransaction.TransactionType.RETURN_RESTOCK,
                                quantity=item.quantity,
                                balance_after=inv.quantity,
                                reference_id=order.order_number,
                                description=f"Restocked from approved return for order {order.order_number}"
                            )

                            release_redis_stock(sku=item.sku_snapshot, quantity=item.quantity)
                        items_restocked = True
                else:
                    items_restocked = False

                merchant_fault_reasons = [
                    ReturnRequest.ReturnReason.DAMAGED,
                    ReturnRequest.ReturnReason.DEFECTIVE,
                    ReturnRequest.ReturnReason.WRONG_ITEM,
                ]

                if return_request.reason_type in merchant_fault_reasons and order.coupon:
                    CouponUsage.objects.filter(order=order, user=order.user).delete()
                    Coupon.objects.filter(id=order.coupon_id, times_used__gt=0).update(times_used=models.F('times_used') - 1)

                order.status = Order.OrderStatus.RETURNED
                order.save(update_fields=['status', 'updated_at'])

                milestone_msg = f"Return approved by admin. Items {'restocked' if items_restocked else 'written off / discarded (damaged)'}."
                if admin_notes:
                    milestone_msg += f"Note: {admin_notes}"

                OrderTrackingMilestone.objects.create(
                    order=order,
                    status=Order.OrderStatus.RETURNED,
                    notes=milestone_msg[:100]
                )
            elif new_status == ReturnRequest.ReturnStatus.REJECTED:
                order.status = Order.OrderStatus.DELIVERED
                order.save(update_fields=['status', 'updated_at'])

                rejection_msg = "Return rejected by admin."
                if admin_notes:
                    rejection_msg += f" Note: {admin_notes}"
                OrderTrackingMilestone.objects.create(
                    order=order,
                    status=Order.OrderStatus.DELIVERED,
                    notes=rejection_msg[:100]
                )

        send_dynamic_email(
            recipient_email=return_request.user.email,
            template_key='RETURN_STATUS',
            fallback_file='return_status.html',
            default_subject=f"Return Request {return_request.get_status_display()}: #{order.order_number}",
            context_dict={
                'order': order,
                'customer_name': getattr(return_request.user, 'name', None) or return_request.user.email,
                'return_status': return_request.status,
                'admin_notes': return_request.admin_notes or ''
            }
        )

        invalidate_dashboard_cache()

        return Response(
            {
                "success": True,
                "message": f"Return request '{new_status.lower()}' successfully.",
                "data": {
                    "return_request_id": return_request.id,
                    "order_number": order.order_number,
                    "return_status": return_request.status,
                    "order_status": order.status,
                    "items_restocked": items_restocked,
                    "admin_notes": return_request.admin_notes,
                    "reviewed_at": return_request.reviewed_at,
                }
            },
            status=status.HTTP_200_OK
        )



class WalletBalanceView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        serializer = WalletBalanceSerializer(wallet)
        return Response(
            {
                "success": True,
                "data": {
                    **serializer.data,
                    "currency": "INR"
                }
            },
            status=status.HTTP_200_OK
        )

class WalletTransactionListView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        queryset = wallet.transactions.all().order_by('-created_at')

        transaction_type = request.query_params.get('type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type.upper())
        source = request.query_params.get('source')
        if source:
            queryset = queryset.filter(source=source.upper())

        paginator = StandardResultsSetPagination()
        paginated_transactions = paginator.paginate_queryset(queryset, request)
        serializer = WalletTransactionSerializer(paginated_transactions, many=True)
        return paginator.get_paginated_response(serializer.data)


class WalletTopUpCreateOrderView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request):
        serializer = WalletTopUpCreateOrderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        amount = serializer.validated_data['amount']
        receipt = f"WAL-{request.user.id}-{int(timezone.now().timestamp())}"

        try:
            razorpay_order = razorpay_client.order.create({
                "amount": int(amount * 100),
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    "user_id": str(request.user.id),
                    "user_email": request.user.email,
                    "type": "WALLET_TOPUP"
                }
            })
        except Exception as e:
            return Response(
                {"success": False, "message": f"Failed to create Razorpay top-up order: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        return Response(
            {
                "success": True,
                "message": "Wallet top-up order created. Proceed with payment on client.",
                "data": {
                    "razorpay_order_id": razorpay_order['id'],
                    "amount": str(amount),
                    "currency": "INR",
                    "razorpay_key": settings.RAZORPAY_KEY_ID,
                }
            },
            status=status.HTTP_200_OK
        )

class WalletTopUpVerifyView(APIView):
    permission_classes = [IsCustomer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'payment_verify'

    def post(self, request):
        serializer = WalletTopUpVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        razorpay_order_id = serializer.validated_data['razorpay_order_id']
        razorpay_payment_id = serializer.validated_data['razorpay_payment_id']
        razorpay_signature = serializer.validated_data['razorpay_signature']

        try:
            razorpay_client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature,
            })
        except razorpay.errors.SignatureVerificationError:
            return Response(
                {"success": False, "message": "Payment verification failed. Invalid signature."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if WalletTransaction.objects.filter(reference_id=razorpay_payment_id).exists():
            return Response(
                {"success": False, "message": "This payment has already been credited to your wallet."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            rp_order = razorpay_client.order.fetch(razorpay_order_id)
            credit_amount = Decimal(rp_order['amount'] / Decimal(100))
        except Exception:
            credit_amount = serializer.validated_data.get('amount')
            if not credit_amount:
                return Response(
                    {"success": False, "message": "Could not determine top-up amount from payment provider."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=request.user)
            trans = wallet.credit(
                amount=credit_amount,
                source=WalletTransaction.TransactionSource.TOP_UP,
                reference_id=razorpay_payment_id,
                description=f"Wallet top-up via Razorpay ({razorpay_payment_id})"
            )

        send_dynamic_email(
            recipient_email=request.user.email,
            template_key='WALLET_TOPUP',
            fallback_file='wallet_topup.html',
            default_subject=f"Wallet Credited: ₹{credit_amount}",
            context_dict={
                'customer_name': getattr(request.user, 'name', None) or request.user.email,
                'amount': str(credit_amount),
                'balance': str(wallet.balance),
                'reference_id': razorpay_payment_id
            }
        )

        return Response(
            {
                "success": True,
                "message": f"Wallet topped up successfully with ₹{credit_amount}.",
                "data": {
                    "balance": str(wallet.balance),
                    "transaction_id": trans.id,
                    "reference_id": razorpay_payment_id,
                    "amount_credited": str(credit_amount),
                    "currency": "INR"
                }
            },
            status=status.HTTP_200_OK
        )


class AdminCouponListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = Coupon.objects.all().order_by('-created_at')

        is_active_param = request.query_params.get('is_active')
        if is_active_param is not None:
            if is_active_param.lower() in ['true', '1']:
                queryset = queryset.filter(is_active=True)
            elif is_active_param.lower() in ['false', '0']:
                queryset = queryset.filter(is_active=False)

        search_query = request.query_params.get('search') or request.query_params.get('q')
        if search_query:
            queryset = queryset.filter(code__icontains=search_query.strip())

        discount_type = request.query_params.get('discount_type')
        if discount_type:
            queryset = queryset.filter(discount_type=discount_type.upper())

        paginator = StandardResultsSetPagination()
        paginated_coupons = paginator.paginate_queryset(queryset, request)
        serializer = CouponSerializer(paginated_coupons, many=True)
        return paginator.get_paginated_response(serializer.data)


    def post(self, request):
        serializer = CouponSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        coupon = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Promotional coupon '{coupon.code}' created successfully.",
                "data": serializer.data
            },
            status=status.HTTP_201_CREATED
        )


class AdminCouponDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        coupon = get_object_or_404(Coupon, pk=pk)
        serializer = AdminCouponDetailSerializer(coupon)
        return Response(
            {
                "success": True,
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )

    def put(self, request, pk):
        return self._update(request, pk, partial=True)
    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        coupon = get_object_or_404(Coupon, pk=pk)
        serializer = CouponSerializer(coupon, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Promotional coupon '{coupon.code}' updated successfully.",
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    def delete(self, request, pk):
        coupon = get_object_or_404(Coupon, pk=pk)
        if not coupon.is_active:
            return Response(
                {
                    "success": False,
                    "message": f"Coupon '{coupon.code}' is already inactive."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        coupon.is_active = False
        coupon.save(update_fields=['is_active'])
        return Response(
            {
                "success": True,
                "message": f"Coupon '{coupon.code}' deactivated successfully. It can no longer be redeemed."
            },
            status=status.HTTP_200_OK
        )


class CustomerEligibleCouponsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        now = timezone.now()
        queryset = Coupon.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now, times_used__lt=models.F('usage_limit')).order_by(
            '-created_at'
        )

        serializer = CustomerCouponSerializer(queryset, many=True, context={'request': request})
        return Response({"success": True, "count": queryset.count(), "data": serializer.data}, status=status.HTTP_200_OK)


class AdminReturnRequestListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = ReturnRequest.objects.select_related('order', 'user').order_by('created_at')

        status_param = request.query_params.get('status')
        if status_param:
            status_param_upper = status_param.upper()
            valid_statuses = [choice[0] for choice in ReturnRequest.ReturnStatus.choices]
            if status_param_upper not in valid_statuses:
                return Response(
                    {
                        "success": False,
                        "message": f"Invalid status '{status_param}. Valid values: {valid_statuses}"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            queryset = queryset.filter(status=status_param_upper)

        reason_type = request.query_params.get('reason_type')
        if reason_type:
            queryset = queryset.filter(reason_type=reason_type.upper())

        search = request.query_params.get('search')
        if search:
            search = search.strip()
            queryset = queryset.filter(
                models.Q(order__order_number__icontains=search) |
                models.Q(user__email__icontains=search) |
                models.Q(user__name__icontains=search)
            )

        paginator = StandardResultsSetPagination()
        paginated_returns = paginator.paginate_queryset(queryset, request)
        serializer = AdminReturnRequestListSerializer(paginated_returns, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminReturnRequestDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        return_request = get_object_or_404(ReturnRequest.objects.select_related(
            'order__user', 'user').prefetch_related(
                'order__items', 'order__milestones', 'order__payments'
            ),
            pk=pk
        )
        serializer = AdminReturnRequestDetailSerializer(return_request)
        return Response(
            {
                "success": True,
                "data": serializer.data
            },
            status=status.HTTP_200_OK
        )


class OrderInvoiceView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'invoice_download'

    def get(self, request, identifier):
        if request.user.role == 'ADMIN':
            queryset = Order.objects.all()
        else:
            queryset = Order.objects.filter(user=request.user)

        queryset = queryset.select_related('user').prefetch_related('items', 'payments')

        if str(identifier).isdigit():
            order = get_object_or_404(queryset, id=int(identifier))
        else:
            order = get_object_or_404(queryset, order_number=identifier)
        
        serializer = OrderInvoiceSerializer(order)
        return Response(
            {
                "success": True, "data": serializer.data
            },
            status=status.HTTP_200_OK
        )
        

class AdminWalletTransactionListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = WalletTransaction.objects.select_related('wallet__user').order_by('-created_at')

        user_id = request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(wallet__user_id=user_id)

        transaction_type = request.query_params.get('type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type.upper())

        source  = request.query_params.get('source')
        if source :
            queryset = queryset.filter(source=source.upper())

        search = request.query_params.get('search') or request.query_params.get('q')
        if search:
            search=search.strip()
            queryset = queryset.filter(
                models.Q(reference_id__icontains=search) |
                models.Q(wallet__user__email__icontains=search) |
                models.Q(wallet__user__name__icontains=search) |
                models.Q(description__icontains=search)
            )

        paginator = StandardResultsSetPagination()
        paginated_transactions = paginator.paginate_queryset(queryset, request)
        serializer = AdminWalletTransactionSerializer(paginated_transactions, many=True)
        return paginator.get_paginated_response(serializer.data)
    

class AdminDashboardMetricsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        valid_periods = ['all', 'today', '7d', '30d']
        period_param = request.query_params.get('period')

        if period_param is not None:
            period = period_param.lower().strip()
            if period not in valid_periods:
                return Response(
                    {
                        "success": False,
                        "message": f"Invalid period '{period_param}'. Valid options are: {valid_periods}"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            period = 'all'

        cache_key = f"dashboard:metrics:{period}"
        cached_response = cache.get(cache_key)
        if cached_response:
            return Response(cached_response, status=status.HTTP_200_OK)

        now = timezone.now()

        date_filter = models.Q()
        if period == 'today':
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            date_filter = models.Q(created_at__gte=today_start)
        elif period == '7d':
            date_filter = models.Q(created_at__gte=now - timedelta(days=7))
        elif period == '30d':
            date_filter = models.Q(created_at__gte=now - timedelta(days=30))
            

        valid_orders = Order.objects.filter(date_filter).exclude(status=Order.OrderStatus.CANCELLED).exclude(
            payment_status=Order.PaymentStatus.FAILED
        )

        booked_sales_agg = valid_orders.aggregate(total=models.Sum('grand_total'))
        booked_sales = booked_sales_agg['total'] or Decimal('0.00')

        collected_cash_agg  = valid_orders.filter(payment_status=Order.PaymentStatus.COMPLETED).aggregate(
            total=models.Sum('grand_total')
        )
        collected_cash = collected_cash_agg['total'] or Decimal(0.00)

        scoped_orders = Order.objects.filter(date_filter)
        totol_orders = scoped_orders.count()
        pending_orders = scoped_orders.filter(status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.CONFIRMED]).count()
        in_transit_orders = scoped_orders.filter(
            status__in=[Order.OrderStatus.SHIPPED, Order.OrderStatus.OUT_FOR_DELIVERY]
        ).count()
        delivered_orders = scoped_orders.filter(status=Order.OrderStatus.DELIVERED).count()
        cancelled_orders = scoped_orders.filter(status=Order.OrderStatus.CANCELLED).count()

        total_customers = User.objects.filter(role=User.Role.CUSTOMER).count()
        active_customers = User.objects.filter(role=User.Role.CUSTOMER, is_active=True).count()
        new_customers_last_30_days = User.objects.filter(
            role=User.Role.CUSTOMER,
            created_at__gte=now - timedelta(days=30)
        ).count()
        new_customers_in_period = User.objects.filter(
            role=User.Role.CUSTOMER).filter(date_filter).count() if period != 'all' else total_customers

        low_stock_count = Inventory.objects.filter(quantity__lte=models.F('low_stock_threshold'), quantity__gt=0).count()
        out_of_stock_count = Inventory.objects.filter(quantity=0).count()

        pending_returns = ReturnRequest.objects.filter(status=ReturnRequest.ReturnStatus.PENDING).count()


        response_payload = {
            "success": True,
            "data": {
                "period": period,
                "revenue": {
                    "booked_sales": str(booked_sales.quantize(Decimal('0.01'))),
                    "collected_cash": str(collected_cash.quantize(Decimal('0.01'))),
                    "currency": "INR"
                },
                "orders": {
                    "total_orders": totol_orders,
                    "pending_orders": pending_orders,
                    "in_transit_orders": in_transit_orders,
                    "delivered_orders": delivered_orders,
                    "cancelled_orders": cancelled_orders
                },
                "customers": {
                    "total_customers": total_customers,
                    "active_customers": active_customers,
                    "new_customers_in_period": new_customers_in_period,
                    "new_customers_last_30_days": new_customers_last_30_days
                },
                "inventory": {
                    "low_stock_variants": low_stock_count,
                    "out_of_stock_variants": out_of_stock_count
                },
                "returns": {
                    "pending_returns": pending_returns
                }
            }
        }
        cache.set(cache_key, response_payload, timeout=60)
        
        return Response(response_payload, status=status.HTTP_200_OK)
    

class AdminDashboardSalesChartView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        valid_timelines = ['week', 'month', 'year']
        timeline_param = request.query_params.get('timeline')
        if timeline_param is not None:
            timeline = timeline_param.lower().strip()
            if timeline not in valid_timelines:
                return Response(
                    {
                        "success": False,
                        "message": f"Invalid timeline '{timeline_param}'. Valid options are: {valid_timelines}"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            timeline = 'week'
        cache_key = f"dashboard:sales_chart:{timeline}"
        cached_response = cache.get(cache_key)
        if cached_response is not None:
            return Response(cached_response, status=status.HTTP_200_OK)

        now = timezone.now()

        valid_orders = Order.objects.exclude(status=Order.OrderStatus.CANCELLED).exclude(
            payment_status=Order.PaymentStatus.FAILED
        )

        chart_data = []
        total_period_revenue = Decimal('0.00')
        total_period_orders = 0

        if timeline in ['week', 'month']:
            days_count = 7 if timeline == 'week' else 30
            start_date = (now - timedelta(days=days_count - 1)).date()
            end_date = now.date()

            aggregated_orders_queryset = (
                valid_orders.filter(created_at__date__gte=start_date).annotate(
                    period=TruncDate('created_at')).values('period').annotate(
                        revenue=models.Sum('grand_total'), orders_count=models.Count('id'))
            )
            sales_by_date_lookup  = {row['period']: row for row in aggregated_orders_queryset if row.get('period')}

            current_calendar_date = start_date
            while current_calendar_date <= end_date:
                sales_row = sales_by_date_lookup.get(current_calendar_date)
                revenue_amount = sales_row['revenue'] if sales_row else Decimal('0.00')
                orders_count = sales_row['orders_count'] if sales_row else 0

                total_period_revenue += revenue_amount
                total_period_orders += orders_count

                chart_data.append({
                    "date": current_calendar_date.strftime('%Y-%m-%d'),
                    "revenue": str(revenue_amount.quantize(Decimal('0.01'))),
                    "orders_count": orders_count 
                })
                current_calendar_date += timedelta(days=1)

        else:
            current_year = now.year
            current_month = now.month
            twelve_month_intervals = []

            for month_offset in range(11, -1, -1):
                target_month = current_month - month_offset
                target_year = current_year
                while target_month <= 0:
                    target_month +=12
                    target_year -= 1
                formatted_month_string = f"{target_year}-{target_month:02d}"
                twelve_month_intervals.append((formatted_month_string, target_year, target_month))

            earliest_year = twelve_month_intervals[0][1]
            earliest_month = twelve_month_intervals[0][2]
            start_datetime = now.replace(year=earliest_year, month=earliest_month,day=1, hour=0, minute=0, second=0, microsecond=0)

            aggregated_orders_queryset = (
                valid_orders.filter(created_at__gte=start_datetime).annotate(period=TruncMonth('created_at')).values('period')
                .annotate(revenue=models.Sum('grand_total'), orders_count=models.Count('id'))
            )
            sales_by_month_lookup = {
                row['period'].strftime('%Y-%m'): row
                for row in aggregated_orders_queryset if row.get('period')
            }

            for formatted_month_string, _, _ in twelve_month_intervals:
                sales_row = sales_by_month_lookup.get(formatted_month_string)
                revenue_amount = sales_row['revenue'] if sales_row else Decimal('0.00')
                orders_count = sales_row['orders_count'] if sales_row else 0

                total_period_revenue += revenue_amount
                total_period_orders += orders_count

                chart_data.append({
                    "data": formatted_month_string,
                    "revenue": str(revenue_amount.quantize(Decimal('0.01'))),
                    "orders_count": orders_count
                })

        response_payload = {
            "success": True,
            "data": {
                "timeline": timeline,
                "summary": {
                    "total_revenue": str(total_period_revenue.quantize(Decimal('0.01'))),
                    "total_orders": total_period_orders
                },
                "chart_data": chart_data
            }
        }

        cache.set(cache_key, response_payload, timeout=60)

        return Response(response_payload, status=status.HTTP_200_OK)