from django.urls import path
# from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    RegisterView,
    VerifyOTPView,
    ResendOTPView,
    LoginAPI,
    ForgotPasswordView,
    ResetPasswordView,
    ResetPasswordFormView,
    LogoutView,
    GoogleAuthView,
    CustomerProfileView,
    ChangePasswordView,
    AddressListCreateView,
    AddressDetailView,
    SetDefaultAddressView,
    CustomTokenRefreshView,
    #admin views
    AdminCustomerListView,
    AdminLoginAPI,
    AdminCustomerDetailView,
    AdminCustomerStatusView,
    
)

urlpatterns = [
    # ==========================
    # 1. Authentication Endpoints (/api/v1/auth/...)
    # ==========================
    path("auth/register", RegisterView.as_view(), name="auth-register"),
    path("auth/verify-otp", VerifyOTPView.as_view(), name="auth-verify-otp"),
    path("auth/send-otp", ResendOTPView.as_view(), name="auth-send-otp"),
    path("auth/resend-otp", ResendOTPView.as_view(), name="auth-resend-otp"),
    path("auth/login", LoginAPI.as_view(), name="auth-login"),
    path("auth/forgot-password", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/reset-password-form", ResetPasswordFormView.as_view(), name="auth-reset-password-form"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("auth/refresh-token", CustomTokenRefreshView.as_view(), name="auth-refresh-token"),
    path("auth/google", GoogleAuthView.as_view(), name="auth-google"),

    # ==========================
    # 2. Customer Profile & Addresses (/api/v1/customer/...)
    # ==========================
    path("customer/profile", CustomerProfileView.as_view(), name="customer-profile"),
    path("customer/change-password", ChangePasswordView.as_view(), name="customer-change-password"),
    path("customer/addresses", AddressListCreateView.as_view(), name="customer-address-list-create"),
    path("customer/addresses/<int:pk>", AddressDetailView.as_view(), name="customer-address-detail"),
    path("customer/addresses/<int:pk>/default", SetDefaultAddressView.as_view(), name="customer-address-set-default"),

    # 3. Admin Customer Management
    path("admin/auth/login", AdminLoginAPI.as_view(), name="admin-auth-login"),
    path("admin/customers", AdminCustomerListView.as_view(), name="admin-customers-list"),
    path("admin/customers/<int:pk>", AdminCustomerDetailView.as_view(), name="admin-customer-detail"),
    path("admin/customers/<int:pk>/status", AdminCustomerStatusView.as_view(), name="admin-customer-status"),
]