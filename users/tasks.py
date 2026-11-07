from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_email_async(self, subject, message, recipient_list, from_email=None, html_message=None):
    """
    Celery background task: Sends email asynchronously with auto-retry.
    If Gmail has a temporary network hiccup, it automatically retries up to 3 times.
    """
    try:
        from_email = from_email or settings.DEFAULT_FROM_EMAIL
        send_mail(
            subject=subject,
            message=message,   # Plain text fallback
            from_email=from_email,
            recipient_list=recipient_list,
            fail_silently=False,
            html_message=html_message,
        )
        logger.info(f"Email successfully sent to {recipient_list}")
        return f"Email sent to {recipient_list}"

    except Exception as exc:
        logger.error(f"Failed to send email to {recipient_list}: {exc}")
        # Retry in 10 seconds if network fails
        raise self.retry(exc=exc)