# Seeds DropdownOption with the values that used to be hardcoded in the
# frontend (Onboarding.jsx), so existing form behavior is unchanged after
# switching to the admin-editable table.

from django.db import migrations

SEED_DATA = {
    "company_type": [
        "Private Limited", "Public Limited", "LLP", "Partnership",
        "Sole Proprietorship", "Government", "Non-Profit",
    ],
    "industry_type": [
        "Retail", "IT / Software", "Manufacturing", "Healthcare",
        "Education", "Finance", "Logistics", "Other",
    ],
    "turnover_range": [
        "< ₹1 Cr", "₹1 Cr - ₹5 Cr", "₹5 Cr - ₹25 Cr", "₹25 Cr - ₹100 Cr", "> ₹100 Cr",
    ],
    "employee_range": ["1-10", "11-50", "51-200", "201-500", "500+"],
    "amc_status": ["Active", "Inactive", "Expired", "Not Applicable"],
    "support_channel": ["Email", "Phone", "Portal", "WhatsApp"],
    "support_time": ["9 AM - 6 PM IST", "24x7", "Custom SLA"],
    "support_type": ["AMC", "NON-AMC", "SAS"],
}


def seed_options(apps, schema_editor):
    DropdownOption = apps.get_model("authentication", "DropdownOption")
    rows = [
        DropdownOption(category=category, value=value, display_order=i, is_active=True)
        for category, values in SEED_DATA.items()
        for i, value in enumerate(values)
    ]
    DropdownOption.objects.bulk_create(rows)


def unseed_options(apps, schema_editor):
    DropdownOption = apps.get_model("authentication", "DropdownOption")
    DropdownOption.objects.filter(category__in=SEED_DATA.keys()).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0014_alter_company_amc_status_alter_company_company_type_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_options, unseed_options),
    ]
