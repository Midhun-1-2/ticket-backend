from django.apps import AppConfig


class ActivityLogConfig(AppConfig):
    """No models of its own — hosts proxy models (see authentication/models.py)
    for LoginActivity and StaffActivityLog, purely so they show as their own
    'Activity Logs' section in the Django admin instead of under Authentication."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'activitylog'
    verbose_name = 'Activity Logs'
