from django.apps import AppConfig


class ReportPasswordConfig(AppConfig):
    """No models of its own — hosts a proxy model (see authentication/models.py)
    for ReportPasswordSettings, purely so it shows as its own 'Report Password'
    section in the Django admin instead of under Authentication."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reportpassword'
    verbose_name = 'Report Password'
