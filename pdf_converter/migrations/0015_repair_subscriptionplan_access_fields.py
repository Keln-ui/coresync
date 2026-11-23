from django.db import migrations


def restore_subscription_plan_access_fields(apps, schema_editor):
    subscription_plan = apps.get_model('pdf_converter', 'SubscriptionPlan')
    table_name = subscription_plan._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

    for field_name in ('includes_pdf_conversion', 'includes_ocr'):
        field = subscription_plan._meta.get_field(field_name)
        if field.column not in columns:
            schema_editor.add_field(subscription_plan, field)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('pdf_converter', '0014_repair_paymentsettings_conversion_cost'),
    ]

    operations = [
        migrations.RunPython(
            restore_subscription_plan_access_fields,
            migrations.RunPython.noop,
        ),
    ]
