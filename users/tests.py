from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from unittest.mock import patch
from django.core.cache import cache

from .models import User, Address
from .tasks import send_email_async 


class AuthenticationTests(APITestCase):
    """
    Automated Unit Tests for Authentication & Customer Security
    """

    def setUp(self):

        """
        Runs before every test: creates a baseline verified customer.
        """

        cache.clear() # Fresh Redis slate for each test!
        self.customer_password = "SecurePassword123!"
        self.customer = User.objects.create_user(
            email="testcustomer@example.com",
            name="TestCustomer",
            password=self.customer_password,
            phone_number="+919876543210",
        )

        # Mark baseline user as verified
        self.customer.is_verified = True
        self.customer.save()

        # Generate a valid JWT token for the customer
        refresh = RefreshToken.for_user(self.customer)
        self.customer_token = str(refresh.access_token)



    # TEST 1: User Registration
    def test_user_registration_success(self):
        """
        Verify that registering a new account:
        1. Returns 201 Created
        2. Creates an unverified user (is_verified=False)
        3. Never saves the password in plain text
        """

        url = reverse("auth-register")
        payload = {
            "name": "Jane Doe",
            "email": "janedoe@example.com",
            "phone_number": "+919123456789",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!",
        }

        response = self.client.post(url, payload, format="json")

        # 1. Check HTTP Status
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 2. Check Database record
        new_user = User.objects.get(email="janedoe@example.com")
        self.assertEqual(new_user.name, "Jane Doe")
        self.assertEqual(new_user.role, User.Role.CUSTOMER)
        self.assertFalse(new_user.is_verified) # Must be unverified initially!

        # 3. Security: Password must be hashed (NOT plain text!)
        self.assertNotEqual(new_user.password, "StrongPassword123!")
        self.assertTrue(new_user.check_password("StrongPassword123!"))


    # TEST 2: Unverified User Login Gate
    def test_unverified_user_cannot_login(self):
        """
        Verify that an unverified user is rejected with 403 Forbidden.
        """

        # Create an unverified user
        unverified_user = User.objects.create_user(
            email="unverified@example.com",
            name="Unverified User",
            password="Password123!",
        )

        # Ensure is_verified is False
        self.assertFalse(unverified_user.is_verified)

        url = reverse("auth-login")
        payload = {
            "email": "unverified@example.com",
            "password": "Password123!",
        }

        response = self.client.post(url, payload, format="json")

        # Must be blocked with 403 Forbidden!
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("error", response.data)


    # TEST 3: Blocked User Security Gate
    def test_blocked_customer_cannot_access_profile(self):
        """
        Verify that if an admin deactivates a customer (is_active=False),
        SimpleJWT immediately invalidates their session with 401 Unauthorized.
        """

        # STEP 1: ARRANGE (Prepare the scenario)
        # ==========================================
        # We want to test what happens when a customer is blocked by an admin.
        # So we take our test customer and set is_active to False:
        # 1. Admin blocks the customer
        self.customer.is_active = False
        self.customer.save()
        
        # 2. Customer tries to access their profile using their JWT token
        # In Postman, you'd type "/api/v1/customer/profile":
        url = reverse("customer-profile")
        # In Postman, you'd paste "Bearer <token>" in the Auth tab:
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        # ==========================================
        # STEP 2: ACT (Click 'Send' in Postman)
        # ==========================================
        response = self.client.get(url)
         # ==========================================
        # STEP 3: ASSERT (Check the result)
        # ==========================================
        # SimpleJWT blocks deactivated users with 401 Unauthorized!
        # Did our security system block them with 401 Unauthorized?
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("detail", response.data)

    # ==========================================
    # TEST 4: Address Auto-Default & Flipping
    # ==========================================
    def test_address_auto_default_and_flipping(self):
        """
        Verify:
        1. 1st address created is automatically marked is_default=True
        2. Adding a 2nd address with is_default=True flips the 1st address to False
        """

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        url = reverse("customer-address-list-create")

        # 1. Create first address (without specifying is_default)
        payload_1 = {
            "recipient_name": "Test Customer",
            "phone_number": "+919876543210",
            "street_address": "123 Main Street",
            "city": "Bangalore",
            "state": "Karnataka",
            "postal_code": "560001",
            "country": "IN",
            "address_type": "HOME",
        }

        res_1 = self.client.post(url, payload_1, format="json")
        self.assertEqual(res_1.status_code, status.HTTP_201_CREATED)

        # first Address Must be automatically true!
        self.assertTrue(res_1.data["address"]["is_default"])
        add_1_id = res_1.data["address"]["id"]

        # 2. Create second address with is_default=True
        payload_2 = {
            "recipient_name": "Test Customer Office",
            "phone_number": "+919876543210",
            "street_address": "456 Tech Park",
            "city": "Bangalore",
            "state": "Karnataka",
            "postal_code": "560100",
            "country": "IN",
            "address_type": "WORK",
            "is_default": True,
        }

        res_2 = self.client.post(url, payload_2, format="json")
        self.assertEqual(res_2.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res_2.data["address"]["is_default"])

        # 3. Verify Address 1 was automatically flipped to False in database
        addr_1 = Address.objects.get(id=add_1_id)
        self.assertFalse(addr_1.is_default)

    # ==========================================
    # TEST 5: IDOR Protection (Ownership Check)
    # ==========================================
    def test_idor_security_cannot_delete_other_user_address(self):
        """
        Verify that Hacker (User B) cannot delete or view Victim's (User A) address.
        """

        # 1. Victim (self.customer) creates an address
        victim_address = Address.objects.create(
            user=self.customer,
            recipient_name="Victim",
            phone_number="+919876543210",
            street_address="Victim House",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
        )

        # 2. Create Hacker (User B) with their own token
        hacker = User.objects.create_user(
            email="hacker@example.com",
            name="Hacker User",
            password="Password123!",
            is_verified=True,
        )

        hacker_token = str(RefreshToken.for_user(hacker).access_token)

        # 3. Hacker tries to delete Victim's address
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {hacker_token}")
        url = reverse("customer-address-detail", kwargs={"pk": victim_address.pk})

        response = self.client.delete(url)

        # Must return 404 Not Found (Hacker cannot even know address exists!)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # confirm address still exists safely in database
        self.assertTrue(Address.objects.filter(id=victim_address.id).exists())


    # ==========================================
    # TEST 6: Admin Login RBAC Check
    # ==========================================
    def test_customer_cannot_login_as_admin(self):
        """
        Verify that a normal customer cannot log in through /admin/auth/login.
        Must return 403 Forbidden.
        """

        url = reverse("admin-auth-login")
        payload = {
            "email": self.customer.email,
            "password": self.customer_password
        }

        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("error", response.data)


    # ==========================================
    # TEST 7: Admin Customer Search Query
    # ==========================================
    def test_admin_customer_search(self):
        """
        Verify that an admin can search customers by name or email.
        """
        # 1. Create an admin user
        admin_user = User.objects.create_superuser(
            email="admin@gmail.com",
            name="super admin",
            password="AdminPassword123!"
        )

        admin_token = str(RefreshToken.for_user(admin_user).access_token)

        # 2. Create another customer with unique name
        User.objects.create_user(
            email="alice@gmail.com",
            name="Alice Smith",
            password="Password123!",
            is_verified = True
        )

        # 3. Admin searches for 'Alice'
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_token}")
        url = reverse("admin-customers-list") + "?search=Alice"

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should return exactly 1 result matching 'Alice'
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Alice Smith")


    # ==========================================
    # TEST 8: Token Blacklist Security Check
    # ==========================================
    def test_logged_out_refresh_token_cannot_refresh(self):
        """
        Security Test:
        1. User logs out (blacklisting their refresh token in Redis)
        2. Attempting to use that blacklisted refresh token MUST be rejected with 401 Unauthorized
        """

        # Step 1: Create a fresh refresh token for the customer
        refresh = RefreshToken.for_user(self.customer)
        refresh_token_str = str(refresh)

        # Step 2: Customer logs out
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        logout_url = reverse("auth-logout")
        logout_res = self.client.post(logout_url, {"refresh_token": refresh_token_str}, format="json")
        self.assertEqual(logout_res.status_code, status.HTTP_200_OK)

        # Step 3: Attacker tries to use the logged-out refresh token to get a new access token
        refresh_url = reverse("auth-refresh-token")
        refresh_res = self.client.post(refresh_url, {"refresh": refresh_token_str}, format="json")

        # Must be rejected with 401 Unauthorized!
        self.assertEqual(refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("error", refresh_res.data)


    # ==========================================
    # TEST 9: Celery Background Email Task (Mocked)
    # ==========================================

    @patch("users.tasks.send_mail")
    def test_celery_email_task_execution(self, mock_send_mail):
        """
        Verify that Celery's send_email_async task calls Django's send_mail correctly.
        We use @patch so no real email is sent over the internet during automated tests!
        """

        # Arrange
        subject = "Test Subject"
        message = "Test Message Body"
        recipients = ["test@example.com"]

        # Act: Execute the task directly
        result = send_email_async(subject, message, recipients)

        # Assert: send_mail must have been called exactly once with our arguments
        mock_send_mail.assert_called_once()
        self.assertEqual(result, f"Email sent to {recipients}")

    # ==========================================
    # TEST 10: Google SSO New User Registration
    # ==========================================
    @patch("google.oauth2.id_token.verify_oauth2_token")
    def  test_google_sso_new_user_registration(self, mock_verify):
        """
        Verify that a brand new user signing in with Google:
        1. Creates a customer account automatically
        2. Sets is_verified=True and sso_provider='GOOGLE'
        3. Returns valid SimpleJWT tokens
        """

        # Mock Google's verification response
        mock_verify.return_value = {
            "email": "googleuser@example.com",
            "name": "Google User",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
            "email_verified": True,
        }

        url = reverse("auth-google")
        response = self.client.post(url, {"id_token": "fake-google-id-token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("tokens", response.data)
        self.assertEqual(response.data["user"]["email"], "googleuser@example.com")
        self.assertEqual(response.data["user"]["sso_provider"], "GOOGLE")

        # Verify database record
        new_user = User.objects.get(email="googleuser@example.com")
        self.assertTrue(new_user.is_verified)
        self.assertTrue(new_user.is_sso_verified)
        self.assertFalse(new_user.has_usable_password())

    # ==========================================
    # TEST 11: Google SSO Existing User Login
    # ==========================================
    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_google_sso_existing_user_login(self, mock_verify):
        """
        Verify that an existing user signing in with Google gets authenticated seamlessly.
        """

        # Mock Google token for our existing test customer
        mock_verify.return_value = {
            "email": self.customer.email,
            "name": self.customer.name,
            "email_verified": True
        }

        url = reverse("auth-google")
        response = self.client.post(url, {"id_token": "fake-google-id-token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["email"], self.customer.email)
        self.assertIn("access", response.data["tokens"])



        # ==========================================
    # TEST 12: Account-Scoped Rate Limiting (NAT Protection)
    # ==========================================
    def test_rate_limiting_per_email_protects_other_customers(self):
        """
        Verify that rate-limiting targets the attacked EMAIL (10 requests/minute),
        ensuring other customers sharing the same IP/Wi-Fi are NOT blocked.
        """
        url = reverse("auth-login")
        target_payload = {
            "email": self.customer.email,
            "password": "WrongPassword!",
        }

        # 1. Fire 10 rapid attempts against self.customer to exhaust the throttle
        for _ in range(10):
            self.client.post(url, target_payload, format="json")

        # 2. The 11th attempt against the same email MUST be blocked by Throttle with 429!
        blocked_res = self.client.post(url, target_payload, format="json")
        self.assertEqual(blocked_res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # 3. An innocent customer on the SAME IP/Client tries to log in:
        innocent_user = User.objects.create_user(
            email="innocent@example.com",
            name="Innocent User",
            password="SecurePassword123!",
            is_verified=True,
        )
        innocent_payload = {
            "email": innocent_user.email,
            "password": "SecurePassword123!",
        }

        # Innocent user MUST NOT be blocked (returns 200 OK)!
        innocent_res = self.client.post(url, innocent_payload, format="json")
        self.assertEqual(innocent_res.status_code, status.HTTP_200_OK)

    # ==========================================
    # TEST 13: Account Lockout Policy
    # ==========================================
    @patch("users.views.send_email_async.delay")
    def test_account_lockout_after_five_failed_attempts(self, mock_celery_email):
        """
        Verify that:
        1. Attempts 1-4 fail with 401
        2. Attempt 5 triggers Account Lockout with 403 and dispatches a Celery security email
        3. Attempt 6 (even with CORRECT password) is STILL blocked with 403 because account is locked!
        """
        url = reverse("auth-login")
        wrong_payload = {
            "email": self.customer.email,
            "password": "WrongPassword123!",
        }

        # Attempts 1 through 4 should fail with 401
        for i in range(1, 5):
            res = self.client.post(url, wrong_payload, format="json")
            self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
            remaining = 5 - i
            self.assertIn(f"{remaining} attempt(s) remaining", res.data["error"])

        # Attempt 5 triggers LOCKOUT (403 Forbidden)
        lockout_res = self.client.post(url, wrong_payload, format="json")
        self.assertEqual(lockout_res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("locked for 15 minutes", lockout_res.data["error"])

        # Verify Celery alert email was dispatched!
        mock_celery_email.assert_called_once()

        # Attempt 6 with CORRECT password must STILL be rejected with 403 (Account is locked)!
        correct_payload = {
            "email": self.customer.email,
            "password": self.customer_password,
        }
        blocked_res = self.client.post(url, correct_payload, format="json")
        self.assertEqual(blocked_res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("temporarily locked", blocked_res.data["error"])