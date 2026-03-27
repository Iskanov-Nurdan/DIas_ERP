from django.db import migrations, models


def forwards_fill_inventory_form(apps, schema_editor):
    WarehouseBatch = apps.get_model('warehouse', 'WarehouseBatch')
    for row in WarehouseBatch.objects.all():
        has_pack = (
            row.pieces_per_package is not None
            and row.pieces_per_package != 0
        ) or (
            row.packages_count is not None
            and row.packages_count != 0
        ) or (
            row.unit_meters is not None
            and row.unit_meters != 0
        )
        row.inventory_form = 'packed' if has_pack else 'unpacked'
        row.save(update_fields=['inventory_form'])


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_warehouse_batch_otk_package'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='inventory_form',
            field=models.CharField(
                choices=[
                    ('unpacked', 'Не упаковано'),
                    ('packed', 'Упаковано'),
                    ('open_package', 'Открытая упаковка'),
                ],
                default='unpacked',
                max_length=20,
                verbose_name='Форма учёта на складе ГП',
            ),
        ),
        migrations.RunPython(forwards_fill_inventory_form, migrations.RunPython.noop),
    ]
