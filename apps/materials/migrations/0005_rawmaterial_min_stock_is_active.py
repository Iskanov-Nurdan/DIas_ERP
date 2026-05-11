from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0004_incoming_total_price'),
    ]

    operations = [
        migrations.RenameField(
            model_name='rawmaterial',
            old_name='min_balance',
            new_name='min_stock',
        ),
        migrations.AddField(
            model_name='rawmaterial',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активен'),
        ),
    ]
