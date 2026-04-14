from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0003_plastic_profile_per_meter'),
    ]

    operations = [
        migrations.AddField(
            model_name='recipe',
            name='comment',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
        migrations.AddField(
            model_name='recipe',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активен'),
        ),
    ]
