"""
Одноразовая демо-наполнялка Dias Line: реалистичные записи по всем страницам
сайдбара (кроме «Сотрудники» — accounts.User/Role/UserAccess не трогаем).

Метод: авторизуемся как существующий пользователь `test` (DRF APIClient,
force_authenticate — он просто подставляет request.user, без обхода бизнес-
логики) и бьём по тем же самым /api/... эндпоинтам, которыми пользуется
фронт. Это гоняет реальные сериализаторы/сервисы (FIFO списание сырья,
проверка кредитного лимита, otk-пул, apply_production_batch_stock_and_cost
и т.п.), а значит и реальные хуки audit_service.schedule_entity_audit —
«Журнал действий» заполняется сам, никакого отдельного сидинга для него нет.

Исключения (намеренно, через прямой ORM .objects.create, НЕ через API):
  - production.Line — у пользователя `test` нет access_key='lines'
    (LineViewSet.required_access_key='lines'), а добавлять ему этот ключ —
    это правка accounts.UserAccess, то есть «Сотрудники», которую нас
    явно попросили не трогать. Line — чистый справочник без движения
    остатков, создание им не через API ничего не искажает.
  - recipes.Recipe/RecipeComponent — аналогично: RecipeViewSet.required_
    access_key='recipes', которого у `test` тоже нет. Рецепт сам по себе
    не списывает склад (списание — в apply_production_batch_stock_and_cost
    при создании ProductionBatch, а это уже идёт через реальный POST
    /api/batches/, который `test` может вызывать по ключу 'production').

Остальные 20+ эндпоинтов (сырьё, приход, цех, ОТК, профили, партии
производства, смены, клиенты, продажи, вся линия Foam, аналитика/расходы)
— только через настоящие вызовы API.

Запуск:  python manage.py seed_demo_data
Повтор:  безопасен для справочников (RawMaterial/WorkshopBlank/PlasticProfile/
Client/FoamDensityGrade ищутся по имени через get_or_create), но каждый
повторный прогон добавит новые приходы/партии/продажи/смены — это ОК.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIClient

TODAY = date(2026, 10, 16)


def d(n_days_ago: int) -> str:
    return (TODAY - timedelta(days=n_days_ago)).isoformat()


class Seeder:
    def __init__(self, stdout):
        self.stdout = stdout
        User = get_user_model()
        self.user = User.objects.filter(name='test').first()
        if self.user is None:
            raise CommandError("Пользователь с name='test' не найден — ничего не делаю.")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def log(self, msg):
        self.stdout.write(msg)

    # ── HTTP helpers ────────────────────────────────────────────────────
    def post(self, path, data, ok=(200, 201)):
        resp = self.client.post(path, data, format='json')
        if resp.status_code not in ok:
            raise CommandError(f'POST {path} -> {resp.status_code}: {resp.data}')
        return resp.data

    def patch(self, path, data, ok=(200,)):
        resp = self.client.patch(path, data, format='json')
        if resp.status_code not in ok:
            raise CommandError(f'PATCH {path} -> {resp.status_code}: {resp.data}')
        return resp.data

    def get(self, path, ok=(200,)):
        resp = self.client.get(path)
        if resp.status_code not in ok:
            raise CommandError(f'GET {path} -> {resp.status_code}: {resp.data}')
        return resp.data

    # ── 1. Сырьё (materials) ────────────────────────────────────────────
    def seed_materials(self):
        self.log('== Сырьё ==')
        from apps.materials.models import RawMaterial

        specs = [
            ('Поливинилхлорид ПВХ-С6359М', 'kg', Decimal('500')),
            ('Стабилизатор Ca/Zn', 'kg', Decimal('50')),
            ('Диоксид титана (TiO2)', 'kg', Decimal('30')),
        ]
        self.rm = {}
        for name, unit, min_balance in specs:
            existing = RawMaterial.objects.filter(name=name).first()
            if existing:
                self.rm[name] = existing.pk
                continue
            row = self.post('/api/raw-materials/', {
                'name': name, 'unit': unit, 'min_balance': str(min_balance), 'is_active': True,
            })
            self.rm[name] = row['id']
            self.log(f"  + RawMaterial #{row['id']}: {name}")

        incoming = [
            ('Поливинилхлорид ПВХ-С6359М', '2000', '450.00', 'ТОО «Полимер Снаб»', d(45)),
            ('Поливинилхлорид ПВХ-С6359М', '1500', '462.00', 'ТОО «Полимер Снаб»', d(10)),
            ('Стабилизатор Ca/Zn', '300', '1850.00', 'ТОО «ХимРесурс Алматы»', d(40)),
            ('Диоксид титана (TiO2)', '150', '2200.00', 'ТОО «ХимРесурс Алматы»', d(40)),
        ]
        for mat_name, qty, price, supplier, received_at in incoming:
            row = self.post('/api/incoming/', {
                'material_id': self.rm[mat_name],
                'quantity': qty,
                'unit_price': price,
                'received_at': received_at,
                'supplier_name': supplier,
                'document_number': f'ПР-{received_at}',
            })
            self.log(f"  + Incoming #{row['id']}: {mat_name} +{qty}кг от {supplier}")

    # ── 2. Заготовка (workshop) ─────────────────────────────────────────
    def seed_workshop(self):
        self.log('== Заготовка ==')
        from apps.workshop.models import WorkshopBlank

        blank_specs = [
            ('Заготовка ПВХ 60х40 белая', [
                (self.rm['Поливинилхлорид ПВХ-С6359М'], '48'),
                (self.rm['Стабилизатор Ca/Zn'], '1.5'),
                (self.rm['Диоксид титана (TiO2)'], '0.5'),
            ]),
            ('Заготовка Штапик 20мм белая', [
                (self.rm['Поливинилхлорид ПВХ-С6359М'], '38'),
                (self.rm['Стабилизатор Ca/Zn'], '1.2'),
                (self.rm['Диоксид титана (TiO2)'], '0.8'),
            ]),
        ]
        self.blanks = {}
        for name, composition in blank_specs:
            existing = WorkshopBlank.objects.filter(name=name).first()
            if existing:
                self.blanks[name] = existing.pk
                continue
            row = self.post('/api/workshop/blanks/', {
                'name': name,
                'composition': [
                    {'raw_material_id': rid, 'quantity_kg': qty} for rid, qty in composition
                ],
            })
            self.blanks[name] = row['id']
            self.log(f"  + WorkshopBlank #{row['id']}: {name} (recipe_kg_per_barrel={row.get('recipe_kg_per_barrel')})")

        # +2 бочки на каждую заготовку (списание сырья FIFO через реальный сервис)
        for name in self.blanks:
            for _ in range(2):
                self.post(f"/api/workshop/prepared-blanks/{self.blanks[name]}/add-barrel/", {}, ok=(200,))
            self.log(f"  + 2 бочки добавлены на «{name}»")

    # ── 3. Производство: Line (ORM-исключение), профили, рецепты ───────
    def seed_production_catalog(self):
        self.log('== Производство (справочники) ==')
        from apps.production.models import Line
        from apps.recipes.models import PlasticProfile, Recipe, RecipeComponent

        self.line, created = Line.objects.get_or_create(
            name='Линия экструзии №1', defaults={'code': 'L1', 'is_active': True},
        )
        self.log(f"  {'+ ' if created else '= '}Line #{self.line.pk}: {self.line.name}")

        profile_specs = [
            ('Профиль ПВХ 60х40 белый, 6м', 'PR-6040W', Decimal('2.0'),
             self.blanks['Заготовка ПВХ 60х40 белая']),
            ('Штапик ПВХ 20мм белый, 6м', 'PR-20W', Decimal('0.6'),
             self.blanks['Заготовка Штапик 20мм белая']),
        ]
        self.profiles = {}
        for name, code, weight, blank_id in profile_specs:
            existing = PlasticProfile.objects.filter(code=code).first()
            if existing:
                self.profiles[name] = existing.pk
                continue
            row = self.post('/api/plastic-profiles/', {
                'name': name, 'code': code, 'weight_kg_per_piece': str(weight),
                'blank_id': blank_id, 'is_active': True,
            })
            self.profiles[name] = row['id']
            self.log(f"  + PlasticProfile #{row['id']}: {name}")

        # Recipe/RecipeComponent — прямой ORM (нет access_key='recipes' у test,
        # см. докстринг модуля); сам рецепт не двигает склад.
        recipe_specs = [
            ('Профиль ПВХ 60х40 белый, 6м', 'ПВХ профиль 60х40 — базовый рецепт', [
                (self.rm['Поливинилхлорид ПВХ-С6359М'], Decimal('0.350000')),
                (self.rm['Стабилизатор Ca/Zn'], Decimal('0.011000')),
            ]),
            ('Штапик ПВХ 20мм белый, 6м', 'Штапик 20мм — базовый рецепт', [
                (self.rm['Поливинилхлорид ПВХ-С6359М'], Decimal('0.120000')),
                (self.rm['Стабилизатор Ca/Zn'], Decimal('0.004000')),
            ]),
        ]
        self.recipes = {}
        for profile_name, recipe_name, components in recipe_specs:
            existing = Recipe.objects.filter(recipe=recipe_name).first()
            if existing:
                self.recipes[profile_name] = existing.pk
                continue
            recipe = Recipe.objects.create(
                recipe=recipe_name,
                profile_id=self.profiles[profile_name],
                base_unit=Recipe.BASE_UNIT_PER_METER,
                is_active=True,
            )
            for rid, qpm in components:
                RecipeComponent.objects.create(
                    recipe=recipe, type=RecipeComponent.TYPE_RAW,
                    raw_material_id=rid, quantity_per_meter=qpm, unit='кг',
                )
            self.recipes[profile_name] = recipe.pk
            self.log(f"  + Recipe #{recipe.pk}: {recipe_name} (ORM, нет access_key=recipes у test)")

    # ── 4. Цех (BlankProductionRun) + ОТК (OtkAccountSession → WarehouseBatch)
    def seed_workshop_floor_and_otk(self):
        self.log('== Цех + ОТК ==')
        run_specs = [
            ('Заготовка ПВХ 60х40 белая', '90', 'Профиль ПВХ 60х40 белый, 6м', 30),
            ('Заготовка Штапик 20мм белая', '60', 'Штапик ПВХ 20мм белый, 6м', 60),
        ]
        self.warehouse_batches = {}
        for blank_name, kg, profile_name, pieces in run_specs:
            run = self.post('/api/workshop/blank-production-runs/', {
                'blank_id': self.blanks[blank_name],
                'blank_total_kg': kg,
                'blank_used_in_production_kg': kg,
                'vat_max_kg_demo': '200',
            })
            self.log(f"  + BlankProductionRun #{run['id']}: {blank_name} {kg}кг в пул ОТК")

            session = self.post('/api/workshop/otk-account/', {
                'lines': [{'profile_id': self.profiles[profile_name], 'pieces': pieces}],
                'defect_kg': '2',
                'shift_period': 'day',
                'operator_id': self.user.pk,
            })
            self.log(f"  + OtkAccountSession #{session['id']}: {profile_name} x{pieces} шт принято на склад")

            from apps.warehouse.models import WarehouseBatch
            wb = WarehouseBatch.objects.filter(otk_account_session_id=session['id']).order_by('-id').first()
            if wb is None:
                raise CommandError(f'WarehouseBatch не создан для сессии ОТК #{session["id"]}')
            self.warehouse_batches[profile_name] = wb.pk
            self.log(f"    -> WarehouseBatch #{wb.pk}: {wb.quantity} шт, себестоимость/шт={wb.cost_per_piece}")

    # ── 5. Смены (shifts/open, batches, shifts/close) ───────────────────
    def seed_shifts_and_production_batches(self):
        self.log('== Смены + Производство (партии) ==')
        cycles = [
            ('Профиль ПВХ 60х40 белый, 6м', 40, '6.0000', 'Утренняя смена: запуск партии 60х40'),
            ('Штапик ПВХ 20мм белый, 6м', 80, '6.0000', 'Дневная смена: штапик 20мм'),
        ]
        for profile_name, pieces, length, note in cycles:
            opened = self.post('/api/shifts/open/', {
                'line_id': self.line.pk, 'height': '60.00', 'width': '40.00', 'angle_deg': '90.00',
                'comment': note, 'session_title': note,
            })
            self.log(f"  + Shift #{opened['id']} открыта на линии «{self.line.name}»")

            batch = self.post('/api/batches/', {
                'profile': self.profiles[profile_name],
                'recipe': self.recipes[profile_name],
                'line': self.line.pk,
                'pieces': pieces,
                'length_per_piece': length,
                'date': TODAY.isoformat(),
                'comment': note,
            })
            self.log(f"    + ProductionBatch #{batch['id']}: {profile_name} x{pieces} шт, "
                     f"total_meters={batch.get('total_meters')}")

            closed = self.post('/api/shifts/close/', {
                'line_id': self.line.pk, 'height': '60.00', 'width': '40.00', 'angle_deg': '90.00',
                'comment': f'{note} — смена закрыта',
            })
            self.log(f"  + Shift #{closed['id']} закрыта")

    # ── 6. Клиенты ───────────────────────────────────────────────────────
    def seed_clients(self):
        self.log('== Клиенты ==')
        from apps.sales.models import Client

        specs = [
            ('ТОО "СтройИндустрия"', 'company', '+7 701 234 5678', '123456789012',
             'г. Алматы, ул. Промышленная 15', '2000000', 'soft'),
            ('ТОО "АлматыПластПром"', 'company', '+7 727 300 1122', '987654321098',
             'г. Алматы, пр. Райымбека 312', '1500000', 'soft'),
            ('ИП Серіков Б.Т.', 'individual', '+7 705 555 9988', '', '', None, 'soft'),
        ]
        self.clients = {}
        for name, ctype, phone, inn, address, credit_limit, mode in specs:
            existing = Client.objects.filter(name=name).first()
            if existing:
                self.clients[name] = existing.pk
                continue
            payload = {
                'name': name, 'client_type': ctype, 'phone': phone, 'is_active': True,
                'credit_limit_mode': mode,
            }
            if inn:
                payload['inn'] = inn
            if address:
                payload['address'] = address
            if credit_limit:
                payload['credit_limit'] = credit_limit
            row = self.post('/api/clients/', payload)
            self.clients[name] = row['id']
            self.log(f"  + Client #{row['id']}: {name}")

    # ── 7. Касса (sales.Sale, профильная линия) ─────────────────────────
    def seed_sales(self):
        self.log('== Касса (профиль) ==')
        # Sale.unit_price обязан точно совпадать с profile.sale_unit_price =
        # cost_price (из FIFO себестоимости заготовки на ОТК) + markup_amount
        # + прочие расходы (apps/sales/profile_sale_price.py). cost_price
        # известен только после OTK-приёмки (посчитан там) — выставляем
        # реалистичную наценку сейчас и берём сервер-посчитанную цену продажи.
        sale_specs = [
            ('Профиль ПВХ 60х40 белый, 6м', 'ТОО "СтройИндустрия"', 15, Decimal('700.00'), 'full', None),
            ('Штапик ПВХ 20мм белый, 6м', 'ТОО "АлматыПластПром"', 25, Decimal('120.00'), 'partial', '5000.00'),
        ]
        for profile_name, client_name, qty, markup, payment_type, paid_amount in sale_specs:
            profile_id = self.profiles[profile_name]
            self.patch(f'/api/plastic-profiles/{profile_id}/', {'markup_amount': str(markup)})
            detail = self.get(f'/api/plastic-profiles/{profile_id}/')
            price = detail['sale_unit_price']

            payload = {
                'date': d(2),
                'client': self.clients[client_name],
                'sale_status': 'shipped',
                'comment': f'Продажа: {profile_name}',
                'sale_lines': [{
                    'product': profile_name,
                    'warehouse_batch': self.warehouse_batches[profile_name],
                    'quantity': str(qty),
                    'unit_price': price,
                    'defect_flag': False,
                }],
                'payment_type': payment_type,
            }
            if paid_amount is not None:
                payload['paid_amount'] = paid_amount
            else:
                payload['paid_amount'] = str((Decimal(price) * qty).quantize(Decimal('0.01')))
            row = self.post('/api/sales/', payload)
            self.log(f"  + Sale #{row['id']}: {profile_name} x{qty} шт @ {price} клиенту «{client_name}», "
                     f"total={row.get('total_amount')}, debt={row.get('debt_amount')}")

        # Доп. платёж в счёт долга по частичной продаже
        from apps.sales.models import Sale
        partial_sale = Sale.objects.filter(client_id=self.clients['ТОО "АлматыПластПром"']).order_by('-id').first()
        if partial_sale is not None:
            pay = self.post('/api/payments/', {
                'date': d(1),
                'client': self.clients['ТОО "АлматыПластПром"'],
                'linked_sale': partial_sale.pk,
                'payment_type': 'payment',
                'amount': '3000.00',
                'payment_method': 'cash',
                'comment': 'Частичная оплата по счёту',
            })
            self.log(f"  + Payment #{pay['id']}: доплата 3000 по Sale #{partial_sale.pk}")

    # ── 8. Пенополистирол (Foam) ────────────────────────────────────────
    def seed_foam(self):
        self.log('== Пенополистирол (Foam) ==')
        from apps.foam.models import FoamDensityGrade

        grade_specs = [('14-15', '13', '15.5'), ('20-22', '18', '22')]
        self.grades = {}
        for code, mn, mx in grade_specs:
            existing = FoamDensityGrade.objects.filter(code=code).first()
            if existing:
                self.grades[code] = existing.pk
                continue
            row = self.post('/api/foam/density-grades/', {'code': code, 'min_kg_m3': mn, 'max_kg_m3': mx})
            self.grades[code] = row['id']
            self.log(f"  + FoamDensityGrade #{row['id']}: {code}")

        # generate_lot_number() считает номер по количеству лотов за ТЕКУЩИЙ
        # календарный месяц (timezone.now()), а не по месяцу received_at —
        # поэтому даты обоих лотов держим в одном (текущем) месяце, иначе
        # обе попытки получат один и тот же номер и упадут в UNIQUE.
        lot1 = self.post('/api/foam/raw-lots/', {
            'material_name': 'Гранула EPS Kingeps HS', 'supplier': 'Kingeps',
            'bag_weight_kg': '800', 'unit_price': '95.00', 'received_at': d(10) + 'T00:00:00Z',
        })
        lot2 = self.post('/api/foam/raw-lots/', {
            'material_name': 'Гранула EPS Alphapor F-300', 'supplier': 'Alphapor',
            'bag_weight_kg': '500', 'unit_price': '102.50', 'received_at': d(3) + 'T00:00:00Z',
        })
        self.log(f"  + FoamRawLot #{lot1['id']}, #{lot2['id']}")

        run_cube = self.post('/api/foam/production-runs/', {
            'lot_id': lot1['id'], 'input_kg': '120', 'output_format': 'cube', 'grade_code': '14-15',
        })
        self.log(f"  + FoamProductionRun #{run_cube['id']}: куб {run_cube['output_qty']} шт")

        run_granule = self.post('/api/foam/production-runs/', {
            'lot_id': lot2['id'], 'input_kg': '60', 'output_format': 'granule',
        })
        self.log(f"  + FoamProductionRun #{run_granule['id']}: гранула {run_granule['output_qty']} кг")

        stock = self.get('/api/foam/gp-stock/')['items']
        cube_row = next(r for r in stock if r['output_format'] == 'cube')
        granule_row = next(r for r in stock if r['output_format'] == 'granule')

        cut = self.post('/api/foam/gp-stock/cut/', {
            'cube_stock_id': cube_row['id'], 'thickness_cm': 5, 'cubes_qty': '2',
        })
        self.log(f"  + Резка кубов на листы: sheet qty={cut['sheet_stock']['qty']}, "
                 f"остаток кубов={cut['cube_stock']['qty']}")

        stock = self.get('/api/foam/gp-stock/')['items']
        sheet_row = next(r for r in stock if r['output_format'] == 'sheet')

        foam_sale_specs = [
            (granule_row['id'], 'ТОО "СтройИндустрия"', '15', '480', None, '7200'),
            (sheet_row['id'], 'ТОО "АлматыПластПром"', '20', '650', '500', '12500'),
            (cube_row['id'], 'ИП Серіков Б.Т.', '1', '9500', None, '9500'),
        ]
        for stock_id, client_name, qty, price, discount, paid in foam_sale_specs:
            payload = {
                'client_id': self.clients[client_name],
                'sale_date': d(1),
                'lines': [{'stock_id': stock_id, 'qty': qty, 'unit_price': price}],
                'paid_amount': paid,
            }
            if discount:
                payload['discount_amount'] = discount
            row = self.post('/api/foam/sales/', payload)
            self.log(f"  + FoamSale #{row['id']}: клиенту «{client_name}», total={row['total_amount']}, "
                     f"debt={row['debt_amount']}")

    # ── 9. Аналитика: категории + ручные расходы ────────────────────────
    def seed_analytics(self):
        self.log('== Аналитика (расходы) ==')
        cat_specs = [('Аренда помещений', 'expense'), ('Транспорт и ГСМ', 'expense')]
        self.cats = {}
        for name, kind in cat_specs:
            resp = self.client.get('/api/analytics/expense-categories/')
            existing_id = None
            items = resp.data.get('items') if isinstance(resp.data, dict) else None
            if items:
                for it in items:
                    if it.get('name') == name:
                        existing_id = it.get('id')
                        break
            if existing_id:
                self.cats[name] = existing_id
                continue
            row = self.post('/api/analytics/expense-categories/', {'name': name, 'kind': kind})
            self.cats[name] = row['id']
            self.log(f"  + ExpenseCategory #{row['id']}: {name}")

        expense_specs = [
            ('Аренда цеха за октябрь', '450000.00', 'Аренда помещений', 'general', d(5)),
            ('Зарплата водителя', '180000.00', None, 'general', d(3)),
            ('ГСМ для доставки сырья', '65000.00', 'Транспорт и ГСМ', 'profile', d(2)),
        ]
        for name, amount, cat_name, product_line, dt in expense_specs:
            payload = {'name': name, 'amount': amount, 'date': dt, 'product_line': product_line, 'kind': 'expense'}
            if cat_name:
                payload['category_id'] = self.cats[cat_name]
            row = self.post('/api/analytics/other-expenses/', payload)
            self.log(f"  + OtherExpense #{row['id']}: {name} — {amount}")

    # ── run all ──────────────────────────────────────────────────────────
    def run(self):
        self.seed_materials()
        self.seed_workshop()
        self.seed_production_catalog()
        self.seed_workshop_floor_and_otk()
        self.seed_shifts_and_production_batches()
        self.seed_clients()
        self.seed_sales()
        self.seed_foam()
        self.seed_analytics()


class Command(BaseCommand):
    help = 'Наполняет БД реалистичными демо-данными через реальные API-эндпоинты (как настоящий сотрудник test).'

    def handle(self, *args, **options):
        # APIClient шлёт Host: testserver — это не изменение бизнес-настроек,
        # просто временно разрешаем локальный host для self-вызовов команды.
        if 'testserver' not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
        seeder = Seeder(self.stdout)
        seeder.run()
        self.stdout.write(self.style.SUCCESS('Готово: демо-данные созданы.'))
