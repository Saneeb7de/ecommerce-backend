import os
from celery import Celery

# 1. Set default Django settings module for celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# 2. Instantiate Celery app
app = Celery('ecommerce_backend')

# 3. Read config from Django settings, using CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# 4. Auto-discover tasks.py across all installed Django apps (e.g. users/tasks.py)
app.autodiscover_tasks()

