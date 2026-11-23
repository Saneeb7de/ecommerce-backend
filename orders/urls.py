from django.urls import path
from .views import (
    AdminDashboardMetricsView, AdminDashboardSalesChartView, AdminWalletTransactionListView, CheckoutPreviewView, OrderCreateListView, OrderDetailView, AdminCouponDetailView,
    OrderCancelView, OrderInvoiceView, OrderTrackingView, OrderReturnRequestView, CustomerEligibleCouponsView,
    RazorpayVerifyPaymentView, RazorpayRetryPaymentView, AdminCouponListCreateView,
    AdminOrderListView, AdminOrderDetailView,AdminOrderStatusUpdateView, AdminReturnModerateView,
    WalletBalanceView, WalletTransactionListView, WalletTopUpCreateOrderView, WalletTopUpVerifyView,
    AdminReturnRequestListView, AdminReturnRequestDetailView
)

urlpatterns = [
    path('orders/checkout-preview', CheckoutPreviewView.as_view(), name='order-checkout-preview'),
    path('orders', OrderCreateListView.as_view(), name='order-create-list'),
    path('orders/<str:identifier>', OrderDetailView.as_view(), name='order-detail'),
    path('orders/<str:identifier>/cancel', OrderCancelView.as_view(), name='order-cancel'),
    path('orders/<str:identifier>/tracking', OrderTrackingView.as_view(), name='order-tracking'),
    path('orders/<str:identifier>/return', OrderReturnRequestView.as_view(), name='order-return'),
    path('orders/<str:identifier>/verify-payment', RazorpayVerifyPaymentView.as_view(), name='order-verify-payment'),
    path('orders/<str:identifier>/retry-payment', RazorpayRetryPaymentView.as_view(), name='order-retry-payment'),
    path('admin/orders', AdminOrderListView.as_view(), name='admin-order-list'),
    path('admin/orders/<int:pk>', AdminOrderDetailView.as_view(), name='admin-order-detail'),
    path('admin/orders/<int:pk>/status', AdminOrderStatusUpdateView.as_view(), name='admin-order-status-update'),
    path('admin/returns/<int:pk>/status', AdminReturnModerateView.as_view(), name='admin-return-moderate'),
    path('wallet/balance', WalletBalanceView.as_view(), name='wallet-balance'),
    path('wallet/transactions', WalletTransactionListView.as_view(), name='wallet-transactions'),
    path('wallet/topup/create-order', WalletTopUpCreateOrderView.as_view(), name='wallet-topup-create-order'),
    path('wallet/topup/verify', WalletTopUpVerifyView.as_view(), name='wallet-topup-verify'),
    path('admin/coupons', AdminCouponListCreateView.as_view(), name='admin-coupon-list-create'),
    path('admin/coupons/<int:pk>', AdminCouponDetailView.as_view(), name='admin-coupon-detail'),
    path('promotions/coupons', CustomerEligibleCouponsView.as_view(), name='customer-eligible-coupons'),
    path('admin/returns', AdminReturnRequestListView.as_view(), name='admin-return-list'),
    path('admin/returns/<int:pk>', AdminReturnRequestDetailView.as_view(), name='admin-return-detail'),
        path('orders/<str:identifier>/invoice', OrderInvoiceView.as_view(), name='order-invoice'),
    path('admin/wallet-transactions', AdminWalletTransactionListView.as_view(), name='admin-wallet-transactions-list'),
    path('admin/dashboard/metrics', AdminDashboardMetricsView.as_view(), name='admin-dashboard-metrics'),
    path('admin/dashboard/sales-chart', AdminDashboardSalesChartView.as_view(), name='admin-dashboard-sales-chart'),
]