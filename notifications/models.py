from django.db import models


class EmailTemplate(models.Model):
    """
    Centralized database-backed email templates editable from Django Super Admin.
    Allows changing email subjects, body copy, festival banners, or promotional
    announcements without server restarts or redeployment.
    """
    class TemplateKey(models.TextChoices):
        # Order Milestones
        ORDER_CONFIRMATION = 'ORDER_CONFIRMATION', 'Order Confirmation'
        ORDER_SHIPPED = 'ORDER_SHIPPED', 'Order Shipped'
        ORDER_DELIVERED = 'ORDER_DELIVERED', 'Order Delivered'
        ORDER_CANCELLED = 'ORDER_CANCELLED', 'Order Cancelled'
        RETURN_STATUS = 'RETURN_STATUS', 'Return Request Status'
        WALLET_TOPUP = 'WALLET_TOPUP', 'Wallet Top-Up'
        # User Authentication Milestones
        OTP_VERIFICATION = 'OTP_VERIFICATION', 'OTP Verification Code'
        PASSWORD_RESET = 'PASSWORD_RESET', 'Password Reset Link'

    template_key = models.CharField(
        max_length=50,
        choices=TemplateKey.choices,
        unique=True,
        db_index=True,
        help_text="Unique key identifier used by the backend system."
    )
    name = models.CharField(max_length=150, help_text="Descriptive title shown in Django Admin.")
    subject = models.CharField(
        max_length=255,
        help_text="Email subject line. Supports variables like {{ order.order_number }} or {{ customer_name }}."
    )
    body_html = models.TextField(
        help_text="Full HTML body. Supports Django template tags: {{ customer_name }}, {{ order.order_number }}, {% for item in items %}, etc."
    )
    body_text = models.TextField(
        blank=True,
        null=True,
        help_text="Plain text fallback for email clients without HTML support."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="If enabled, this database template is used. If disabled, system falls back to default."
    )
    available_variables = models.TextField(
        blank=True,
        editable=False,
        help_text="Reference cheatsheet of available variables."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'email_templates'
        verbose_name = 'Email Template'
        verbose_name_plural = 'Email Templates'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.template_key}) - {'Active' if self.is_active else 'Inactive'}"