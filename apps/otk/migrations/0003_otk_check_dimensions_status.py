from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def forwards_otk(apps, schema_editor):
    OtkCheck = apps.get_model('otk', 'OtkCheck')
    PB = apps.get_model('production', 'ProductionBatch')
    for c in OtkCheck.objects.all().iterator():
        b = PB.objects.filter(pk=c.batch_id).first()
        if not b:
            continue
        c.pieces = int(getattr(b, 'pieces', None) or 0) or 0
        c.length_per_piece = getattr(b, 'length_per_piece', None) or Decimal('0')
        c.total_meters = getattr(b, 'total_meters', None) or Decimal('0')
        if getattr(b, 'profile_id', None):
            c.profile_id = b.profile_id
        acc = Decimal(str(c.accepted or 0))
        rej = Decimal(str(c.rejected or 0))
        if rej > 0 and acc == 0:
            c.check_status = 'rejected'
        elif acc > 0:
            c.check_status = 'accepted'
        else:
            c.check_status = 'pending'
        c.save(
            update_fields=['pieces', 'length_per_piece', 'total_meters', 'profile_id', 'check_status']
        )


class Migration(migrations.Migration):

    dependencies = [
        ('otk', '0002_otkcheck_comment'),
        ('recipes', '0003_plastic_profile_per_meter'),
        ('production', '0019_plastic_batch_and_shift_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='otkcheck',
            name='check_status',
            field=models.CharField(
                choices=[('pending', 'Ожидает'), ('accepted', 'Принято'), ('rejected', 'Брак')],
                default='pending',
                max_length=12,
                verbose_name='Результат',
            ),
        ),
        migrations.AddField(
            model_name='otkcheck',
            name='length_per_piece',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Длина штуки, м'),
        ),
        migrations.AddField(
            model_name='otkcheck',
            name='pieces',
            field=models.PositiveIntegerField(default=0, verbose_name='Штук'),
        ),
        migrations.AddField(
            model_name='otkcheck',
            name='profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='otk_checks',
                to='recipes.plasticprofile',
            ),
        ),
        migrations.AddField(
            model_name='otkcheck',
            name='total_meters',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Всего м'),
        ),
        migrations.AlterField(
            model_name='otkcheck',
            name='accepted',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Принято (legacy)'),
        ),
        migrations.AlterField(
            model_name='otkcheck',
            name='rejected',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Брак (legacy)'),
        ),
        migrations.RunPython(forwards_otk, migrations.RunPython.noop),
    ]
