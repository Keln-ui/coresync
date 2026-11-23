from django.db import migrations


def restore_conversion_cost_column(apps, schema_editor):
    payment_settings = apps.get_model('pdf_converter', 'PaymentSettings')
    table_name = payment_settings._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

    field = payment_settings._meta.get_field('conversion_cost_per_page')
    if field.column not in columns:
        schema_editor.add_field(payment_settings, field)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('pdf_converter', '0013_jobadvertisement_approved'),
    ]

    operations = [
        migrations.RunPython(
            restore_conversion_cost_column,
            migrations.RunPython.noop,
        ),
    ]
