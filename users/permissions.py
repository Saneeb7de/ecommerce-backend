from rest_framework.permissions import BasePermission

class IsCustomer(BasePermission):
    """
    Allows access only to authenticated users with role = CUSTOMER.
    Blocked customers (is_active=False) are automatically denied.
    """

    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.role == 'CUSTOMER' and
            request.user.is_active
        )

class IsAdmin(BasePermission):
    """
    Allows access only to authenticated users with role = ADMIN.
    """
    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            request.user.role == 'ADMIN'
        )