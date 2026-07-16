from django.apps import AppConfig


class EmailContactConfig(AppConfig):
    """No models of its own — hosts a proxy model (see authentication/models.py)
    for CompanyContactSettings, purely so it shows as its own 'Email Contact
    Details' section in the Django admin instead of under Authentication."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'emailcontact'
    verbose_name = 'Email Contact Details'
