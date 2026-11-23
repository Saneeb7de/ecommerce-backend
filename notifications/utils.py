import logging
from django.conf import settings
from django.template import Template, Context
from django.template.loader import render_to_string
from users.tasks import send_email_async
from .models import EmailTemplate

logger = logging.getLogger(__name__)


def send_dynamic_email(recipient_email, template_key, fallback_file, default_subject, context_dict):
    
    try:
        db_tpl = EmailTemplate.objects.filter(template_key=template_key, is_active=True).first()

        html_message = None
        subject = default_subject
        plain_message = None

        if db_tpl:
            try:
                subject = Template(db_tpl.subject).render(Context(context_dict))
                html_message = Template(db_tpl.body_html).render(Context(context_dict))
                if db_tpl.body_text:
                    plain_message = Template(db_tpl.body_text).render(Context(context_dict))
            except Exception as render_err:
                logger.warning(f"Error rendering DB template '{template_key}': {render_err}. Falling back to file.")
                html_message = None

        # Fallback to local file if DB template is inactive or failed
        if not html_message:
            html_message = render_to_string(f"emails/{fallback_file}", context_dict)

        if not plain_message:
            plain_message = subject

        # Push to existing Celery queue
        send_email_async.delay(
            subject=subject,
            message=plain_message,
            recipient_list=[recipient_email],
            from_email=settings.DEFAULT_FROM_EMAIL,
            html_message=html_message
        )
        logger.info(f"Queued dynamic email '{template_key}' for {recipient_email}")

    except Exception as exc:
        logger.error(f"Failed to queue email '{template_key}' for {recipient_email}: {exc}")
        if settings.DEBUG:
            print(f"[EMAIL DEV ERROR] {exc}")