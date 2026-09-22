# Generated manually for blank_id + extra expenses on PlasticProfile

from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def link_profiles_to_workshop_blanks(apps, schema_editor):
    PlasticProfile = apps.get_model('recipes', 'PlasticProfile')
    WorkshopBlank = apps.get_model('workshop', 'WorkshopBlank')
    for blank in WorkshopBlank.objects.exclude(plastic_profile_id__isnull=True):
        PlasticProfile.objects.filter(pk=blank.plastic_profile_id, blank_id__isnull=True).update(
            blank_id=blank.pk
        )


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0008_migrate_otk_pool_from_runs'),
        ('recipes', '0011_plasticprofile_cost_price_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='plasticprofile',
            name='blank',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='plastic_profiles',
                to='workshop.workshopblank',
                verbose_name='Заготовка для ОТК',
            ),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='extra_rubber',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12, verbose_name='Резинка, сом/шт'),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='extra_label',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12, verbose_name='Этикетка, сом/шт'),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='extra_labor',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12, verbose_name='Рабочая сила, сом/шт'),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='extra_electricity',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12, verbose_name='Свет, сом/шт'),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='extra_repair',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12, verbose_name='Ремонт, сом/шт'),
        ),
        migrations.RunPython(link_profiles_to_workshop_blanks, migrations.RunPython.noop),
    ]
