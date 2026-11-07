from rest_framework.throttling import SimpleRateThrottle

# class AuthEmailThrottle(SimpleRateThrottle):
#     """
#     Enterprise Throttle: Rate-limits based on the targeted EMAIL address.
#     Protects users against brute-force attacks without blocking innocent
#     customers sharing the same Wi-Fi/Office IP address (NAT issue).
#     """
#     scope = 'auth_email'

#     def get_cache_key(self, request, view):
#         if request.method != 'POST':
#             return None

#         # Extract targeted email from payload
#         email = request.data.get('email', '')
#         if isinstance(email, str) and email.strip():
#             email_clean = email.strip().lower()
#             # Redis key will be: throttle_auth_email:<email>
#             return self.cache_format % {'scope': self.scope, 'ident': email_clean}

#         return self.get_ident(request)


# class OTPSendThrottle(SimpleRateThrottle):
#     """
#     Rate-limits OTP generation requests per email (e.g. max 3 requests per 5 minutes).
#     Prevents SMTP abuse and SMS flooding.
#     """

#     scope = 'otp_send'

#     def get_cache_key(self, request, view):
#         if request.method != 'POST':
#             return None

#         email = request.data.get('email', '')
#         if isinstance(email, str) and email.strip():
#             return self.cache_format % {'scope': self.scope, 'ident': email.strip().lower()}

#         return self.get_ident(request)





# almost same class why cant we inherit?
#so create a base class and uinherit both because oonly change is 'scope'

class BaseEmailRateThrottle(SimpleRateThrottle):
    """
    Base Throttle: Scopes rate limiting to the targeted EMAIL address in Redis.
    Prevents innocent users on shared Wi-Fi (college/office NAT IP) from being blocked.
    Subclasses only need to specify their own `scope`.
    """

    def get_cache_key(self, request, view):
        # Only throttle POST requests
        if request.method != 'POST':
            return None

        # Extract and clean email from request payload
        email = request.data.get('email', '')
        if isinstance(email, str) and email.strip():
            return self.cache_format % {'scope': self.scope, 'ident': email.strip().lower()}
        # Fallback to client IP if payload has no email field
        return self.get_ident(request)

# Subclasses: Inherit all logic, only define the unique scope!
class AuthEmailThrottle(BaseEmailRateThrottle):
    """Max 5 failed logins per minute per email"""
    scope = 'auth_email'

class OTPSendThrottle(BaseEmailRateThrottle):
    """Max 3 OTP requests per 5 minutes per email"""
    scope = 'otp_send'