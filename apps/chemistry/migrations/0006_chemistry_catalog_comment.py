from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chemistry', '0005_chemistry_batches_recipe_rename'),
    ]

    operations = [
        migrations.AddField(
            model_name='chemistrycatalog',
            name='comment',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
    ]
