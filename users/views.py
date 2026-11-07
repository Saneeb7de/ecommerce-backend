from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
import secrets
from django .views import View
from django.core.cache import cache
from django.shortcuts import get_object_or_404, render
from django.contrib.auth import authenticate
from django.db.models import Q
from django.conf import settings
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.template.loader import render_to_string
from django.db import transaction

from .models import User, Address
from .serializers import UserRegistrationSerializer, OTPSendSerializer, OTPVerifySerializer, LoginSerializer, ForgotPasswordSerializer, ResetPasswordSerializer, UserProfileSerializer, ChangePasswordSerializer, AddressSerializer
from .serializers import AdminCustomerDetailSerializer, AdminCustomerListSerializer, GoogleAuthSerializer
from .utils import send_otp_via_email, verify_otp_code
from .permissions import IsCustomer, IsAdmin
from .pagination import StandardResultsSetPagination
from rest_framework_simplejwt.views import TokenRefreshView
from .tasks import send_email_async
from .throttles import AuthEmailThrottle, OTPSendThrottle


class RegisterView(APIView):
    """
    Public endpoint: Registers new customer with is_verified=False
    and triggers an OTP to their email
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            # 1. Save user to database (default role=CUSTOMER, is_verified=False)
            user = serializer.save()
            # 2. Trigger 6-digit OTP stored in Redis
            send_otp_via_email(user.email)

            return Response(
                {
                    "message": "Registration successfull. Please verify the 6-digit OTP sent to your email.",
                    "email": user.email,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class VerifyOTPView(APIView):
    """
    Public endpoint: Validates OT against Redis.
    If valid: marks user is verified and returns JWT tokens immediately.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"].lower()
        otp = serializer.validated_data["otp"]

        # 1. Verify against Redis (checks expiration & max 3 attempts)
        is_valid, message = verify_otp_code(email, otp)
        if not is_valid:
            return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Find user in PostgreSQL
        # try:
        #     user = User.objects.get(email=email)
        # except User.DoesNotExist:
        #     return Response(
        #         {
        #             "error": "User with this email is does not exist."
        #         },
        #         status=status.HTTP_404_NOT_FOUND,
        #     )
        # commented out and removed the  manually checking everywhere and using the inbuilt shortcut - 
        user = get_object_or_404(User, email=email)

        # 3. Mark user as verified
        user.is_verified = True
        user.save(update_fields=["is_verified"])

        # 4. Generate JWT tokens so user is immediately logged in
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Account verified successfully",
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "is_verified": user.is_verified,
                },
            },
            status=status.HTTP_200_OK
        )

class ResendOTPView(APIView):

    """
    Public endpoint: Dispatches  fresh OTP if the 60s cooldown has passed.
    """

    permission_classes = [AllowAny]
    throttle_classes = [OTPSendThrottle]

    def post(self, request):
        serializer = OTPSendSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"].lower()

        # 1. Check if user exists in database
        user = get_object_or_404(User, email=email)

        # 2. Check if user is already verified
        if user.is_verified:
            return Response(
                {
                    "message": "This account is already verified. You can log in directly"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Attempt to send fresh OTP (enforces 60s cooldown in Redis)
        success, message = send_otp_via_email(email)
        if not success:
            return Response(
                {
                    "error": message
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        return Response(
            {
                "message": message,
            },
            status=status.HTTP_200_OK
        )


class LoginAPI(APIView):
    """
    Public endpoint: Authenticates customer credentials and returns JWT tokens.
    Guards against unverified and blocked accounts
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthEmailThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"].lower()
        password = serializer.validated_data["password"]

        lockout_key = f"account_lockout:{email}"
        attempts_key = f"failed_login_attempts:{email}"

        # Check if Account is currently LOCKED
        if cache.get(lockout_key):
            return Response(
                {
                    "error": "This account is temporarily locked due to 5 consecutive failed login attempts."
                             "Please try again in 15 minutes or reset your password."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # 1. Check if user exists
        user = authenticate(request, email=email, password=password)
        # user = get_object_or_404(User, email=email)
        if not user:
            # Increment failed attempts in Redis (15-min TTL)
            failed_attempts = cache.get(attempts_key, 0) + 1
            cache.set(attempts_key, failed_attempts, timeout=900)

            # Check if threshold reached
            if failed_attempts >= 5:
                # Lock the account for 15 minutes
                cache.set(lockout_key, True, timeout=900)
                cache.delete(attempts_key)

            # Trigger Celery Security Alert Email
                alert_subject = "Security Alert: Account Temporarily Locked"
                alert_message = (
                        f"Hello,\n\n"
                        f"We detected 5 consecutive failed login attempts on your account.\n"
                        f"For your protection, your account has been locked for 15 minutes.\n\n"
                        f"If this was not you, we strongly recommend resetting your password immediately."
                    )

                try:
                    send_email_async.delay(alert_subject, alert_message, [email])
                except Exception as e:
                    print(f"[CELERY QUEUE ERROR] Failed to queue lockout alert: {e}")

                return Response(
                    {
                        "error": "Your account has been locked for 15 minutes due to 5 failed login attempts. "
                                    "A security alert has been sent to your email."
                    }, 
                    status=status.HTTP_403_FORBIDDEN
                )

            remaining = 5 - failed_attempts
            return Response(
                {
                    "error": f"Invalid email or password. You have {remaining} attempt(s) remaining before your account is locked."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        cache.delete(attempts_key)

        # 3. Check verification status
        if not user.is_verified:
            return Response(
                {
                    "error": "Your email is not verified. Please verify your email first"
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # 5. Generate fresh JWT tokens
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Login Successful.",
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                },
            },
            status=status.HTTP_200_OK
        )


class ForgotPasswordView(APIView):
    """
    Public endpoint: Generates a 15-minutes reset token stored in Redis
    and emails a reset link to the user
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        email = serializer.validated_data["email"].lower()

        # 1. Check if user exists
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                # Security Note: Return a generic message so attackers cannot guess registered emails
                {
                    "message": "A password reset link has been sent." 
                },
                status=status.HTTP_200_OK,
            )

        # 2. Generate a secure, URL-safe random token
        reset_token = secrets.token_urlsafe(32)

        # 3. Store in Redis: key="password_reset:<token>", value=email, TTL=900s (15 mins)
        cache.set(f"password_reset:{reset_token}", user.email, timeout=900)

        # 4. Build the full reset link — token is embedded as a URL query parameter
        reset_link = f"{settings.FRONTEND_RESET_URL}?token={reset_token}"

        # 5. Dispatch Password Reset Email via Celery
        reset_subject = "Reset Your Password - E-Commerce Store"
        plain_message = (
            f"Hello {user.name},\n\n"
            f"Click the link below to reset your password:\n\n"
            f"{reset_link}\n\n"
            f"This link will expire in 15 minutes.\n\n"
            f"If you did not request a password reset, please ignore this email."
        )

        html_message = render_to_string("emails/password_reset_email.html", {"user_name": user.name, "reset_link": reset_link}) # full clickable URL — not the raw token

        try:
            send_email_async.delay(reset_subject, plain_message, [user.email], html_message=html_message)

        except Exception as e:
            print(f"[CELERY QUEUE ERROR] Failed to queue password reset email: {e}")

        # 5. In development, also print the reset token to the terminal
        if settings.DEBUG:
            print(f"\n>>> [DEV] Password reset link for {user.email}:\n{reset_link}\n<<<\n")

        return Response(
            {
                "message": "If this email is registered, a password reset link has been sent.",
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):
    """
    public endpoint: Validates reset token against Redis and updates password.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]

        # 1. Look up token in Redis
        token_key = f"password_reset:{token}"
        email = cache.get(token_key)

        if not email:
            return Response(
                {
                    "error": "Invalid or Exprired password reset token."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Find the user
        user = get_object_or_404(User, email=email)

        # 3. Securely hash and update the new password
        user.set_password(new_password)
        user.save(update_fields=["password"])

        # 4. Delete the token from Redis
        cache.delete(token_key)
        cache.delete(f"account_lockout:{user.email}")

        return Response(
            {
                "message": "Password reset successfully. You can now log in with your new password."
            },
            status=status.HTTP_200_OK
        )

class ResetPasswordFormView(View):
    """
    Serves the HTML password reset form page.
    The user arrives here by clicking the link in their password reset email.
    Django renders the page — the page's JS then calls POST /api/v1/auth/reset-password.
    When a real frontend (React/Vue) is built, just update FRONTEND_RESET_URL in .env.
    """
    def get(self, request):
        return render(request, "reset_password_form.html")


class LogoutView(APIView):
    """
    Protected endpoint: Invalidated the user's refresh token via Redis blacklist.
    Requires: Authorization Bearer <access_token> in header.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh_token")
        if not refresh_token:
            return Response(
                {
                    "error": "refresh token is required in request body."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            # blacklist token by saving its unque jti in redis for 7 days (604800 seconds)
            jti = token["jti"]
            blacklist_key = f"blacklist_token:{jti}"

            if cache.get(blacklist_key):
                return Response(
                    {"error": "This token has already been logged out."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cache.set(blacklist_key, True, timeout=7*24*60*60)
            return Response(
                {
                    "message": "Logged out successfully. Token invalidated."
                },
                status=status.HTTP_200_OK
            )

        except Exception:
            return Response(
                {
                    "error": "Invalid or expired refresh token."
                },
                status=status.HTTP_400_BAD_REQUEST
            )


class CustomTokenRefreshView(TokenRefreshView):
    """
    Public endpoint: Exchanges a valid refresh token for a new access token.
    Security Check: Verifies against Redis to ensure the token hasn't been blacklisted via Logout.
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        # Support both 'refresh' and 'refresh_token' keys in JSON body
        raw_token = request.data.get("refresh") or request.data.get("refresh_token")
        if not raw_token:
            return Response(
                {
                    "error": "Refresh token is required"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            token = RefreshToken(raw_token)
            jti = token["jti"]

            # 1. Check Redis Blacklist: Was this token invalidated by Logout?
            if cache.get(f"blacklist_token:{jti}"):
                return Response(
                    {
                        "error": "This refresh token has been logged out and invalidated."
                    },
                    status=status.HTTP_401_UNAUTHORIZED
                )

        except Exception:
            return Response(
                {
                    "error": "Invalid or expired refresh token."
                },
                status=status.HTTP_401_UNAUTHORIZED
            )

        serializer = self.get_serializer(data={"refresh": raw_token})
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {
                    "error": "Invalid or expired refresh token."
                },
                status=status.HTTP_401_UNAUTHORIZED
            )

        return Response(serializer.validated_data, status=status.HTTP_200_OK)




class GoogleAuthView(APIView):
    """
    Public endpoint: Authenticates or registers a user via Google Single Sign-On (SSO).
    Expects a signed Google ID Token from the client, verifies it cryptographically with Google,
    and returns SimpleJWT access and refresh tokens.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        raw_id_token = serializer.validated_data["id_token"]

        # 1. Cryptographically verify the token with Google
        try:
            client_id = settings.GOOGLE_CLIENT_ID or None
            # If client_id is set, verify against it; otherwise verify structure and signature
            id_info = id_token.verify_oauth2_token(raw_id_token, google_requests.Request(), client_id)

        except Exception as e:
            return Response(
                {
                    "error": "Invalid or expired Google authentication token."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Extract verified Google user info
        email = id_info.get("email", "").lower()
        name = id_info.get("name", "")
        picture = id_info.get("picture", "")
        is_email_verified = id_info.get("email_verified", False)

        if not email or not is_email_verified:
            return Response(
                {
                    "error": "Google account does not have a verified email address."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Check if user already exists or needs to be registered
        user = User.objects.filter(email=email).first()

        if user:
            # Block check: If admin blocked this user, reject them!
            if not user.is_active:
                return Response(
                    {
                        "error": "Your account has been deactivated / blocked. Please contact support."
                    },
                    status=status.HTTP_403_FORBIDDEN
                )

            # Auto-verify existing email accounts if they sign in via Google
            if not user.is_verified:
                user.is_verified = True
                user.is_sso_verified = True
                user.save(update_fields=["is_verified", "is_sso_verified"])

            # Optionally update profile picture if missing
            if picture and not user.profile_image:
                user.profile_image = picture
                user.save(update_fields=["profile_image"])

        else:
            # 4. Register new user through Google SSO (password=None, is_verified=True)
            user = User.objects.create_user(
                email=email,
                name=name if name else email.split("@")[0],
                password=None, # Sets unusable password automatically
                role=User.Role.CUSTOMER,
                sso_provider=User.SSOProvider.GOOGLE,
                is_sso_verified=True,
                is_verified=True,
                profile_image=picture if picture else None,
            )

        # 5. Issue SimpleJWT tokens
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Google authentication successful.",
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "sso_provider": user.sso_provider,
                    "profile_image": user.profile_image,
                },
            },
            status=status.HTTP_200_OK
        )
        



class CustomerProfileView(APIView):
    """
    Protected endpoint: View and update authenticated customer profile.
    Requires: Authorization Bearer <token>
    """

    permission_classes = [IsCustomer]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        # partial=True allows updating only one or two fields (e.g. only name or only phone)
        serializer = UserProfileSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "message": "Profile updated successfully",
                    "user": serializer.data
                },
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request):
        return self.put(request)

class ChangePasswordView(APIView):
    """
    Protected endpoint: Allows logged-in customer to change password
    after verifying their current (old) password.
    Requires: Authorization Bearer <token>
    """

    permission_classes = [IsCustomer]

    def put(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]

        # 1. Verify old password against database hash
        if not request.user.check_password(old_password):
            return Response({"error": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)

        # Hash and update new password
        request.user.set_password(new_password)
        request.user.save(update_fields=["password"])

        return Response({
            "message": "Password changed Successfully"
        }, status=status.HTTP_200_OK)

    def patch(self, request):
        return self.put(request)

class AddressListCreateView(APIView):
    """
    Protected endpoint: 
    GET - List all shipping addresses for authenticated customer.
    POST - Add a new shipping address.
    """

    permission_classes = [IsCustomer]

    def get(self, request):
        addresses = Address.objects.filter(user=request.user).order_by("-is_default", "-created_at")
        serializer = AddressSerializer(addresses, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AddressSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        is_default = serializer.validated_data.get("is_default", False)

        # Smart UX: If this is their first address ever, make it default automatically
        if not Address.objects.filter(user=request.user).exists():
            is_default = True

        # used this for to not both address  to defualt or not default 
        with transaction.atomic():
        # If this address is set as default, unmark all other addresses
            if is_default:
                Address.objects.filter(user=request.user, is_default=True).update(is_default=False)

            # Save with user attached from JWT token
            address = serializer.save(user=request.user, is_default=is_default)

        return Response(
            {
                "message": "Address added successfully",
                "address": AddressSerializer(address).data
            },
            status=status.HTTP_201_CREATED
        )


class AddressDetailView(APIView):
    """
    Protected endpoint:
    PUT/PATCH -> Update an existing address.
    DELETE    -> Remove an address.
    """

    permission_classes = [IsCustomer]

    def put(self, request, pk):
        # Enforce ownership: only the owner can update this address
        address = get_object_or_404(Address, pk=pk, user=request.user)
        serializer = AddressSerializer(address, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # If user is changing this address to default, unset other defaults
        if serializer.validated_data.get("is_default") is True:
            Address.objects.filter(user=request.user, is_default=True).exclude(pk=address.pk).update(is_default=False)

        updated_address = serializer.save()
        return Response(
            {
                "message": "Address updated successfully",
                "address": AddressSerializer(updated_address).data,
            },
            status=status.HTTP_200_OK
        )

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        address = get_object_or_404(Address, pk=pk, user=request.user)
        address.delete()
        return Response(
            {"message": "Address deleted successfully."},
            status=status.HTTP_200_OK,
        )


class SetDefaultAddressView(APIView):
    """
    Protected endpoint: 
    PATCH - set a address as the primary checkout address.
    """

    permission_classes = [IsCustomer]

    def patch(self, request, pk):
        address = get_object_or_404(Address, pk=pk, user=request.user)

        # used this for to not both address  to defualt or not default 
        with transaction.atomic():
            # 1. Unset all other default addresses for this customer
            Address.objects.filter(user=request.user, is_default=True).update(is_default=False)

        # 2. Mark this address as default
            address.is_default = True
            address.save(update_fields=["is_default"])

        return Response(
            {
                "message": "Primary default address updated successfully.",
                "address": AddressSerializer(address).data
            },
            status=status.HTTP_200_OK,
        )



#admin side
class AdminLoginAPI(APIView):
    """
    Public endpoint: Authenticates store administrators and isssues admin JWT tokens.
    Rejects normal customers with 403 Forbidden
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"].lower()
        password = serializer.validated_data["password"]

        # 1. Authenticate credentials
        # 1. Find the user
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"error": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)

        # 2. Check hashed password
        if not user.check_password(password):
            return Response({"error": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)
        
        # user = authenticate(request, email=email, password=password)
        # if not user:
        #     return Response(
        #         {
        #             "error": "Invalid administrative credentials."
        #         },
        #         status=status.HTTP_401_UNAUTHORIZED
        #     )

        # 3. Strict Role Check: Reject normal customers!
        if user.role != User.Role.ADMIN:
            return Response(
                {
                    "error": "Access denied. Administrator privileges required."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if not user.is_active:
            return Response(
                {
                    "error": "This administrator account has been deactivated.",
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # 4. Generate tokens (Postman captures {{adminToken}} from this)
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Admin login successful.",
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role
                },
            },
            status=status.HTTP_200_OK
        )


class AdminCustomerListView(APIView):
    """
    Protected endpoint: 
    GET -> Lists all registered customers sorted latest first,
           with optional search (?search=) and pagination (?page=1).
    """

    permission_classes = [IsAdmin]

    def get(self, request):
        # 1. Fetch only customers, sorted latest registered first
        queryset = User.objects.filter(role=User.Role.CUSTOMER).order_by("-created_at")

        # 2. Search filtering (?search=john)
        search_query = request.query_params.get("search")
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(email__icontains=search_query) | Q(phone_number__icontains=search_query))

        # 3. Backend Pagination (10 per page)
        paginator = StandardResultsSetPagination()
        paginated_customers = paginator.paginate_queryset(queryset, request)
        serializer = AdminCustomerListSerializer(paginated_customers, many=True)
        return paginator.get_paginated_response(serializer.data)

class AdminCustomerDetailView(APIView):
    """
    Protected Admin endpoint:
    GET -> Inspect full details of a single customer, including their saved addresses.
    """

    permission_classes = [IsAdmin]

    def get(self, request, pk):
        customer = get_object_or_404(User, pk=pk, role=User.Role.CUSTOMER)
        serializer = AdminCustomerDetailSerializer(customer)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminCustomerStatusView(APIView):
    """
    Protected Admin endpoint:
    PATCH -> Block (deactivate) or Unblock (activate) a customer account.
    """

    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        customer = get_object_or_404(User, pk=pk, role=User.Role.CUSTOMER)

        is_active = request.data.get("is_active")
        if is_active is None:
            return Response(
                {
                    "error": "'is_active' boolean field (true/false) is required."
                },
                status=status.HTTP_400_BAD_REQUEST
            ) 

        # update active status
        customer.is_active = bool(is_active)
        customer.save(update_fields=["is_active"])

        action = "activated (unblocked)" if customer.is_active else "deactivated (blocked)"

        return Response(
            {
                "message": f"Customer accounthas been {action} successfully",
                "customer": {
                    "id": customer.id,
                    "email": customer.email,
                    "is_active": customer.is_active,
                },
            },
            status=status.HTTP_200_OK
        )


