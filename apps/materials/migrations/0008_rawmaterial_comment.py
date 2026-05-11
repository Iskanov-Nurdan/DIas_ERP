from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0007_material_refine_incoming_received_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='rawmaterial',
            name='comment',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
    ]
