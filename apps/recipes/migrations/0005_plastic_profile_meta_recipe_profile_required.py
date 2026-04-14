import django.db.models.deletion
from django.db import migrations, models


def fill_plastic_profile_codes(apps, schema_editor):
    PlasticProfile = apps.get_model('recipes', 'PlasticProfile')
    used = set()
    for p in PlasticProfile.objects.order_by('id'):
        c = (p.code or '').strip() or f'P{p.pk}'
        base = c
        n = 0
        while c in used:
            n += 1
            c = f'{base}-{n}'
        used.add(c)
        c = c[:100]
        if c != p.code:
            p.code = c
            p.save(update_fields=['code'])


def assign_recipe_profiles(apps, schema_editor):
    Recipe = apps.get_model('recipes', 'Recipe')
    PlasticProfile = apps.get_model('recipes', 'PlasticProfile')
    qs = Recipe.objects.filter(profile_id__isnull=True)
    if not qs.exists():
        return
    prof = PlasticProfile.objects.order_by('id').first()
    if prof is None:
        prof, _ = PlasticProfile.objects.get_or_create(
            code='MIG-DEFAULT',
            defaults={'name': 'Профиль (миграция)'},
        )
    Recipe.objects.filter(profile_id__isnull=True).update(profile_id=prof.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0004_recipe_comment_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='plasticprofile',
            name='comment',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активен'),
        ),
        migrations.RunPython(fill_plastic_profile_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='plasticprofile',
            name='code',
            field=models.CharField(max_length=100, verbose_name='Код'),
        ),
        migrations.AddConstraint(
            model_name='plasticprofile',
            constraint=models.UniqueConstraint(fields=('code',), name='plastic_profiles_code_key'),
        ),
        migrations.RunPython(assign_recipe_profiles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='recipe',
            name='profile',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recipes',
                to='recipes.plasticprofile',
                verbose_name='Профиль',
            ),
        ),
    ]
