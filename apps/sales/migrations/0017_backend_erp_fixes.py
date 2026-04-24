# Generated manually for ERP backend fix plan

from django.db import migrations, models
from django.db.models import F
import django.db.models.deletion
import django.utils.timezone


def set_sale_warehouse_applied(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    Sale.objects.filter(warehouse_batch_id__isnull=False).update(warehouse_stock_applied=True)


def fill_sale_updated_at(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    Sale.objects.filter(updated_at__isnull=True).update(updated_at=F('created_at'))
    Sale.objects.filter(updated_at__isnull=True).update(updated_at=django.utils.timezone.now())


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0016_rename_order_res_line_status_idx_order_reser_order_l_db2c6c_idx_and_more'),
        ('warehouse', '0007_remove_warehousebatch_is_defect'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='warehouse_stock_applied',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Списание со склада применено'),
        ),
        migrations.AddField(
            model_name='sale',
            name='credit_limit_bypassed',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Согласован обход кредитного лимита'),
        ),
        migrations.AddField(
            model_name='sale',
            name='updated_at',
            field=models.DateTimeField(null=True, blank=True, verbose_name='Обновлено'),
        ),
        migrations.RunPython(fill_sale_updated_at, noop_reverse),
        migrations.AlterField(
            model_name='sale',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, verbose_name='Обновлено'),
        ),
        migrations.AddField(
            model_name='return',
            name='status',
            field=models.CharField(
                choices=[('draft', 'Черновик'), ('completed', 'Проведён'), ('canceled', 'Отменён')],
                db_index=True,
                default='completed',
                max_length=20,
                verbose_name='Статус',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='status',
            field=models.CharField(
                choices=[('active', 'Активна'), ('canceled', 'Отменена')],
                db_index=True,
                default='active',
                max_length=20,
                verbose_name='Статус записи',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='linked_return',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_payments',
                to='sales.return',
                verbose_name='Связанный возврат (для refund)',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='manual_refund_reason',
            field=models.TextField(blank=True, default='', verbose_name='Причина ручного возврата без Return'),
        ),
        migrations.AlterField(
            model_name='defectrecord',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('otk', 'ОТК'),
                    ('qc', 'ОТК / контроль качества'),
                    ('warehouse', 'Склад'),
                    ('return', 'Возврат клиента'),
                    ('manual', 'Вручную'),
                ],
                default='otk',
                max_length=20,
                verbose_name='Источник',
            ),
        ),
        migrations.AddField(
            model_name='defectrecord',
            name='warehouse_batch',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='linked_defect_record',
                to='warehouse.warehousebatch',
                verbose_name='Партия склада (брак)',
            ),
        ),
        migrations.AlterField(
            model_name='reworkrequest',
            name='return_doc',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='rework_requests',
                to='sales.return',
                verbose_name='Возврат',
            ),
        ),
        migrations.RunPython(set_sale_warehouse_applied, noop_reverse),
    ]
