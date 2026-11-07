from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Address


class AddressInline(admin.StackedInline):
    """
    Shows a customer's saved shipping addresses directly inside their User admin page!
    """
    model = Address
    extra = 0
    fields = (
        "recipient_name",
        "phone_number",
        "street_address",
        "apartment_suite",
        "city",
        "state",
        "postal_code",
        "country",
        "address_type",
        "is_default",
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin configuration for Custom User Model (Email-based authentication).
    """
    ordering = ("-created_at",)
    list_display = (
        "email",
        "name",
        "role",
        "sso_provider",
        "is_verified",
        "is_active",
        "is_staff",
        "created_at",
    )
    list_filter = (
        "role",
        "sso_provider",
        "is_verified",
        "is_active",
        "is_staff",
    )
    search_fields = ("email", "name", "phone_number")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Account Credentials", {
            "fields": ("email", "password"),
        }),
        ("Personal Information", {
            "fields": ("name", "phone_number", "profile_image"),
        }),
        ("Role & Permissions", {
            "fields": ("role", "is_verified", "is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        ("Single Sign-On (SSO)", {
            "fields": ("sso_provider", "sso_organization", "is_sso_verified"),
        }),
        ("Important Dates", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "name", "role", "password"),
        }),
    )

    inlines = [AddressInline]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    """
    Dedicated Address Management in Django Admin.
    """
    ordering = ("-created_at",)
    list_display = (
        "recipient_name",
        "user",
        "city",
        "state",
        "country",
        "address_type",
        "is_default",
        "created_at",
    )
    list_filter = ("address_type", "is_default", "country", "state")
    search_fields = (
        "recipient_name",
        "user__email",
        "user__name",
        "street_address",
        "city",
        "postal_code",
    )
    raw_id_fields = ("user",)