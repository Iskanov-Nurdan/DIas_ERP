"""Бизнес-константы линии «Пенополистирол» — см. BACKEND_FOAM_REQUIREMENTS.md §1."""
from decimal import Decimal

LOSS_RATE = Decimal('0.035')  # технологические потери при обработке, 3.5%
CUBE_VOLUME_M3 = Decimal('1.2')  # 0.6м × 1м × 2м
CUBE_HEIGHT_CM = 60  # высота куба для расчёта числа листов при нарезке

RAW_WAREHOUSE_LABEL = 'Склад сырья №2 — Пенополистирол'
GP_WAREHOUSE_LABEL = 'Склад ГП — Пенопласт'

OUTPUT_FORMAT_CUBE = 'cube'
OUTPUT_FORMAT_SHEET = 'sheet'
OUTPUT_FORMAT_GRANULE = 'granule'

OUTPUT_FORMAT_CHOICES = [
    (OUTPUT_FORMAT_CUBE, 'Куб'),
    (OUTPUT_FORMAT_SHEET, 'Лист'),
    (OUTPUT_FORMAT_GRANULE, 'Гранулы на продажу'),
]

# Форматы, которые реально выпускаются производством (лист получается только нарезкой на складе)
PRODUCTION_OUTPUT_FORMATS = (OUTPUT_FORMAT_CUBE, OUTPUT_FORMAT_GRANULE)

OPERATION_KIND_CHOICES = [
    ('production_intake', 'Поступление с производства'),
    ('sale', 'Продажа'),
    ('defect', 'Брак'),
    ('return', 'Возврат'),
    ('cut_in', 'Нарезка листов — получено'),
    ('cut_out', 'Нарезка листов — списан куб'),
]

PAYMENT_STATUS_CHOICES = [
    ('paid', 'Оплачено'),
    ('partial', 'Частично оплачено'),
    ('debt', 'Долг'),
]
