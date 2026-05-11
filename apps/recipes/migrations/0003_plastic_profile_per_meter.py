# Generated manually for plastic profile + per-meter recipe norms

from django.db import migrations, models
import django.db.models.deletion


def forwards_profiles(apps, schema_editor):
    Recipe = apps.get_model('recipes', 'Recipe')
    PlasticProfile = apps.get_model('recipes', 'PlasticProfile')
    for r in Recipe.objects.all().iterator():
        if getattr(r, 'profile_id', None):
            continue
        name = (getattr(r, 'product', None) or getattr(r, 'recipe', None) or 'Профиль').strip() or 'Профиль'
        prof, _ = PlasticProfile.objects.get_or_create(name=name[:255], defaults={'code': ''})
        r.profile_id = prof.pk
        r.save(update_fields=['profile_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0002_recipe_output_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlasticProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Наименование')),
                ('code', models.CharField(blank=True, default='', max_length=100, verbose_name='Код')),
            ],
            options={
                'verbose_name': 'Профиль',
                'verbose_name_plural': 'Профили',
                'db_table': 'plastic_profiles',
            },
        ),
        migrations.AddField(
            model_name='recipe',
            name='base_unit',
            field=models.CharField(
                choices=[('per_meter', 'На 1 метр')],
                default='per_meter',
                max_length=20,
                verbose_name='База норм',
            ),
        ),
        migrations.AddField(
            model_name='recipe',
            name='profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recipes',
                to='recipes.plasticprofile',
                verbose_name='Профиль',
            ),
        ),
        migrations.AlterField(
            model_name='recipe',
            name='product',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Продукт (денормализация)'),
        ),
        migrations.RenameField(
            model_name='recipecomponent',
            old_name='quantity',
            new_name='quantity_per_meter',
        ),
        migrations.AlterField(
            model_name='recipecomponent',
            name='quantity_per_meter',
            field=models.DecimalField(decimal_places=6, max_digits=14, verbose_name='На 1 м профиля'),
        ),
        migrations.RunPython(forwards_profiles, migrations.RunPython.noop),
    ]
