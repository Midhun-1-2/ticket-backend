from django.apps import AppConfig


class DropdownOptionsConfig(AppConfig):
    """No models of its own — hosts a proxy model (see authentication/models.py)
    for DropdownOption, purely so it shows as its own 'Dropdown Options' section
    in the Django admin instead of under Authentication."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dropdownoptions'
    verbose_name = 'Dropdown Options'
