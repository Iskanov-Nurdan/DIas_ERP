# Generated manually: JSON снимки отката склада + piece_pick у строки продажи

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0018_alter_defectrecord_source_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='warehouse_mutation',
            field=models.JSONField(
                blank=True,
                default=None,
                editable=False,
                null=True,
                verbose_name='Снимок списания склада для отката',
            ),
        ),
        migrations.AddField(
            model_name='saleline',
            name='piece_pick',
            field=models.CharField(
                blank=True,
                default='',
                max_length=40,
                verbose_name='Источник штук (packed/…)',
            ),
        ),
    ]
