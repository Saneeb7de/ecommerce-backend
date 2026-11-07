from django.core.cache import cache
import random
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string

from .tasks import send_email_async

def generate_otp():
    """
    Generates a secure 6 digit numerical string.
    """
    return str(random.randint(100000, 999999))

def send_otp_via_email(email):
    """
    Checks the 60s cooldown, generates OTP, saves to Redis, and sends email.
    Returns: (success: bool, message: str)
    """
    cooldown_key = f"otp_cooldown:{email}"

    # 1. Check if user is in cooldown period
    if cache.get(cooldown_key):
        return False, "Please wait 60 seconds before requesting another OTP."

    # 2. Generate 6-digit OTP
    otp = generate_otp()

    otp_key = f"otp:{email}"
    attempts_key = f"otp_attempts:{email}"

    # 3. Store OTP in Redis (Expires in 5 minutes)
    cache.set(otp_key, otp, timeout=300)
    # 4. Set 60-second cooldown in Redis
    cache.set(cooldown_key, True, timeout=60)
    # 5. Initialize failed attempts counter
    cache.set(attempts_key, 0, timeout=300)

    # 6. Trigger Celery Asynchronous Email Task
    subject = "Your Verification Code - E-Commerce Store"
    plain_message = f"Your verification code is: {otp}\n\nThis code will expire in 5 minutes."
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@store.com')

    # Render modern HTML email template
    html_message = render_to_string("emails/otp_email.html", {"otp": otp})

    try:
        # .delay() pushes the task into Redis in 1 millisecond and returns immediately!
        #  Passes plain message (fallback) + rich HTML template to Celery
        send_email_async.delay(subject, plain_message, [email], from_email, html_message=html_message)
        # send_mail(subject, message, from_email, [email], fail_silently=False) # it is commented because it is done before adding celery, it is for synchronous email sending
    except Exception as e:
        # Fallback print for local development in terminal
        print(f"[CELERY QUEUE ERROR] Failed to queue email task: {e}")
    finally:
        # ALWAYS print to terminal in development mode for easy testing!
        if settings.DEBUG:
            print(f"\n=======================================================")
            print(f" [DEV MODE OTP] To: {email} | Code: {otp}")
            print(f"=======================================================\n")

    return True, "OTP sent successfully to your email"

def verify_otp_code(email, submitted_otp):
    """
    Verifies submitted OTP against Redis with brute-force protection (max 3 attempts).
    """
    otp_key = f"otp:{email}"
    attempts_key = f"otp_attempts:{email}"
    cached_otp = cache.get(otp_key)

    # 1. Check if OTP is expired or doesn't exist
    if not cached_otp:
        return False, "OTP has expired or was never requested, Please request a new one."

    # 2. Check failed attempts (brute-force protection)
    attempts = cache.get(attempts_key, 0)
    if attempts >= 3:
        # Delete OTP immediately if max attempts exceeded
        cache.delete(otp_key)
        cache.delete(attempts_key)

        return False, "Maximum verification attempts exceeded. Please request a new OTP."

    # 3. Verify the code
    if cached_otp != submitted_otp:
        # Increment failed attempts
        cache.set(attempts_key, attempts + 1, timeout=300)
        remaining = 3 - (attempts + 1)
        return False, f"Invalid OTP. {remaining} attempt(s) remaining."

    cache.delete(otp_key)
    cache.delete(attempts_key)
    cache.delete(f"otp_cooldown:{email}")

    return True, "OTP verified Successfully"