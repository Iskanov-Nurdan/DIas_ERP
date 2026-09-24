from django.db import migrations

EXPENSES = [
    ('Аренда', False), ('Коммунальные услуги', False), ('Зарплата', True), ('Ремонт и обслуживание', False),
    ('Топливо и транспорт', False), ('Налоги и сборы', False), ('Штрафы', False), ('Связь и интернет', False),
    ('Прочие расходы', False),
]
INCOMES = ['Прочий доход', 'Аренда (сдача)', 'Продажа отходов']


def seed(apps, schema_editor):
    Category = apps.get_model('analytics', 'AnalyticsExpenseCategory')
    for i, (name, payroll) in enumerate(EXPENSES):
        Category.objects.get_or_create(kind='expense', name=name, defaults={'is_payroll': payroll, 'sort_order': i})
    for i, name in enumerate(INCOMES):
        Category.objects.get_or_create(kind='income', name=name, defaults={'sort_order': i})


class Migration(migrations.Migration):
    dependencies = [('analytics', '0002_expense_registry')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
