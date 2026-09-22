from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0009_plastic_profile_weight_and_optional_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='plasticprofile',
            name='cost_price',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=16,
                verbose_name='Себестоимость за 1 шт, сом',
            ),
        ),
        migrations.AddField(
            model_name='plasticprofile',
            name='markup_amount',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=16,
                verbose_name='Наценка за 1 шт, сом',
            ),
        ),
    ]
