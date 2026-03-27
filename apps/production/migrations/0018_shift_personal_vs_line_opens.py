from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0017_recipe_run_consumption_applied'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='shift',
            name='uniq_shift_one_open_per_user',
        ),
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(closed_at__isnull=True) & models.Q(line_id__isnull=True),
                name='uniq_shift_personal_open_per_user',
            ),
        ),
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(
                fields=['user', 'line_id'],
                condition=models.Q(closed_at__isnull=True) & models.Q(line_id__isnull=False),
                name='uniq_shift_user_line_open_per_user_line',
            ),
        ),
    ]
