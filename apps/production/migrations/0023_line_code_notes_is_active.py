from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0022_production_batch_lifecycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='line',
            name='code',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Код'),
        ),
        migrations.AddField(
            model_name='line',
            name='notes',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
        migrations.AddField(
            model_name='line',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активна'),
        ),
    ]
