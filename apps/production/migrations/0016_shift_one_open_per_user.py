from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def merge_duplicate_open_shifts(apps, schema_editor):
    Shift = apps.get_model('production', 'Shift')
    now = timezone.now()
    dup_users = (
        Shift.objects.filter(closed_at__isnull=True, user_id__isnull=False)
        .values('user_id')
        .annotate(c=Count('id'))
        .filter(c__gt=1)
    )
    for row in dup_users:
        uid = row['user_id']
        pks = list(
            Shift.objects.filter(user_id=uid, closed_at__isnull=True)
            .order_by('-opened_at')
            .values_list('pk', flat=True)
        )
        for pk in pks[1:]:
            Shift.objects.filter(pk=pk).update(closed_at=now)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0015_shift_complaint'),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_open_shifts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(closed_at__isnull=True),
                name='uniq_shift_one_open_per_user',
            ),
        ),
    ]
