from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0006_warehousebatch_quality_defect'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='warehousebatch',
            name='is_defect',
        ),
    ]
