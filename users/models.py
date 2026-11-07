from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

class UserManager(BaseUserManager):
    """Custom user manager where email is the unique identifier for auth."""
    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError("The email must be set")
        email = self.normalize_email(email)
        extra_fields.setdefault('role', 'CUSTOMER')
        user = self.model(email=email, name=name, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault('role', 'ADMIN')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_verified', True)

        if extra_fields.get('role') != 'ADMIN':
            raise ValueError("Superuser must have role=ADMIN.")
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, name, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    """
    Unified identity model strictly for Customers and Administrators.
    Supports local email/password and Google OAuth2 Single Sign-On.
    """
    class Role(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        ADMIN = 'ADMIN', 'Administrator'

    class SSOProvider(models.TextChoices):
        LOCAL = 'LOCAL', 'Local'
        GOOGLE = 'GOOGLE', 'Google'

    name = models.CharField(max_length=100)
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    password = models.CharField(max_length=128, null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    profile_image = models.URLField(max_length=500, null=True, blank=True)
    sso_provider = models.CharField(max_length=30, choices=SSOProvider.choices, default=SSOProvider.LOCAL)
    sso_organization = models.CharField(max_length=100, null=True, blank=True)
    is_sso_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.name} ({self.role})"


class Address(models.Model):
    """
    Customer saved shipping and delivery addresses
    """
    class AddressType(models.TextChoices):
        HOME = 'HOME', 'Home'
        WORK = 'WORK', 'Work'
        OTHER = 'OTHER', 'Other'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    recipient_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)
    street_address = models.CharField(max_length=255)
    apartment_suite = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=2, default='IN')
    address_type = models.CharField(max_length=20, choices=AddressType.choices, default=AddressType.HOME)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'addresses'
        verbose_name = 'Address'
        verbose_name_plural = 'Addresses'

    def __str__(self):
        return f"{self.recipient_name} - {self.city} ({'Default' if self.is_default else 'Alternate'})"