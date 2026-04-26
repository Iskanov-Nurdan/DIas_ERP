from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client, DefectRecord, Order, Payment, Return, ReturnLine, ReworkRequest, Sale, SaleLine
from apps.warehouse.models import WarehouseBatch


class ReturnsApiContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='returns-admin@example.com',
            password='pass12345',
            name='Returns Admin',
        )
        self.client.force_authenticate(self.user)

        self.client_active = Client.objects.create(name='ОсОО Альфа', is_active=True)
        self.order = Order.objects.create(
            order_number='ORD-2026-001',
            date=date(2026, 4, 26),
            client=self.client_active,
            status=Order.STATUS_SHIPPED,
        )
        self.batch = WarehouseBatch.objects.create(
            product='60 мм белый',
            quantity=Decimal('10'),
            date=date(2026, 4, 26),
            status=WarehouseBatch.STATUS_SHIPPED,
            quality=WarehouseBatch.QUALITY_GOOD,
        )
        self.sale_shipped = Sale.objects.create(
            order_number='ORD-2026-SHIP',
            sale_number='SALE-2026-001',
            sale_status=Sale.STATUS_SHIPPED,
            linked_order=self.order,
            client=self.client_active,
            warehouse_batch=self.batch,
            product='60 мм белый',
            quantity=Decimal('10'),
            price=Decimal('100'),
            revenue=Decimal('1000'),
            date=date(2026, 4, 26),
        )
        self.line_shipped = SaleLine.objects.create(
            sale=self.sale_shipped,
            product='60 мм белый',
            warehouse_batch=self.batch,
            quantity=Decimal('10'),
            unit_price=Decimal('100'),
            line_total=Decimal('1000'),
        )
        self.sale_draft = Sale.objects.create(
            order_number='ORD-2026-DR',
            sale_number='SALE-2026-DR',
            sale_status=Sale.STATUS_DRAFT,
            linked_order=self.order,
            client=self.client_active,
            product='Драфт',
            quantity=Decimal('5'),
            price=Decimal('50'),
            revenue=Decimal('250'),
            date=date(2026, 4, 26),
        )
        self.sale_canceled = Sale.objects.create(
            order_number='ORD-2026-CN',
            sale_number='SALE-2026-CN',
            sale_status=Sale.STATUS_CANCELED,
            linked_order=self.order,
            client=self.client_active,
            product='Отмена',
            quantity=Decimal('5'),
            price=Decimal('50'),
            revenue=Decimal('250'),
            date=date(2026, 4, 26),
        )
        self.sale_closed = Sale.objects.create(
            order_number='ORD-2026-CL',
            sale_number='SALE-2026-CL',
            sale_status=Sale.STATUS_CLOSED,
            linked_order=self.order,
            client=self.client_active,
            product='Закрытая',
            quantity=Decimal('8'),
            price=Decimal('120'),
            revenue=Decimal('960'),
            date=date(2026, 4, 26),
        )
        self.line_closed = SaleLine.objects.create(
            sale=self.sale_closed,
            product='Закрытая',
            quantity=Decimal('8'),
            unit_price=Decimal('120'),
            line_total=Decimal('960'),
        )
        self.other_sale = Sale.objects.create(
            order_number='ORD-2026-OT',
            sale_number='SALE-2026-OT',
            sale_status=Sale.STATUS_SHIPPED,
            linked_order=self.order,
            client=self.client_active,
            product='Другой',
            quantity=Decimal('3'),
            price=Decimal('99'),
            revenue=Decimal('297'),
            date=date(2026, 4, 26),
        )
        self.other_line = SaleLine.objects.create(
            sale=self.other_sale,
            product='Другой',
            quantity=Decimal('3'),
            unit_price=Decimal('99'),
            line_total=Decimal('297'),
        )

    def _payload(self, **overrides):
        data = {
            'date': '2026-04-26',
            'sale': self.sale_shipped.pk,
            'linked_order': self.order.pk,
            'return_reason': 'Возврат',
            'invoice_number': 'RET-INV-001',
            'comment': 'test',
            'lines': [
                {
                    'sale_line': self.line_shipped.pk,
                    'quantity': '2',
                    'return_target': 'warehouse',
                    'condition_type': 'good',
                    'comment': '',
                },
            ],
        }
        data.update(overrides)
        return data

    def _create_return(self, **overrides):
        return self.client.post('/api/returns/', data=self._payload(**overrides), format='json')

    def test_create_validation_rules(self):
        no_sale = self._payload()
        no_sale.pop('sale')
        resp = self.client.post('/api/returns/', data=no_sale, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_SALE')

        no_lines = self._create_return(lines=[])
        self.assertEqual(no_lines.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(no_lines.data.get('code'), 'MISSING_LINES')

        no_sale_line = self._create_return(lines=[{'quantity': '1', 'return_target': 'warehouse', 'condition_type': 'good'}])
        self.assertEqual(no_sale_line.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(no_sale_line.data.get('code'), ('MISSING_SALE_LINE', 'MISSING_LINES'))

        wrong_sale_line = self._create_return(lines=[{'sale_line': self.other_line.pk, 'quantity': '1'}])
        self.assertEqual(wrong_sale_line.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_sale_line.data.get('code'), 'SALE_LINE_NOT_IN_SALE')

        qty_zero = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '0'}])
        self.assertEqual(qty_zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(qty_zero.data.get('code'), 'INVALID_QUANTITY')

        qty_exceeded = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '100'}])
        self.assertEqual(qty_exceeded.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(qty_exceeded.data.get('code'), 'RETURN_QUANTITY_EXCEEDED')

        status_in_create = self._create_return(status='completed')
        self.assertEqual(status_in_create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(status_in_create.data.get('code'), 'RETURN_STATUS_CREATE_FORBIDDEN')

        bad_draft = self.client.post('/api/returns/', data=self._payload(sale=self.sale_draft.pk), format='json')
        self.assertEqual(bad_draft.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_draft.data.get('code'), 'INVALID_SALE_STATUS')

        bad_canceled = self.client.post('/api/returns/', data=self._payload(sale=self.sale_canceled.pk), format='json')
        self.assertEqual(bad_canceled.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_canceled.data.get('code'), 'INVALID_SALE_STATUS')

        ok = self.client.post('/api/returns/', data=self._payload(sale=self.sale_closed.pk, lines=[{'sale_line': self.line_closed.pk, 'quantity': '1'}]), format='json')
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ok.data['status'], Return.STATUS_DRAFT)

    def test_update_rules(self):
        created = self._create_return()
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        rid = created.data['id']

        status_patch = self.client.patch(f'/api/returns/{rid}/', data={'status': Return.STATUS_COMPLETED}, format='json')
        self.assertEqual(status_patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(status_patch.data.get('code'), 'RETURN_STATUS_UPDATE_FORBIDDEN')

        draft_ok = self.client.patch(
            f'/api/returns/{rid}/',
            data={'comment': 'upd', 'lines': [{'sale_line': self.line_shipped.pk, 'quantity': '1.5', 'return_target': 'defect', 'condition_type': 'defect'}]},
            format='json',
        )
        self.assertEqual(draft_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(Return.objects.get(pk=rid).lines.count(), 1)

        completed = self.client.post(f'/api/returns/{rid}/complete/', data={}, format='json')
        self.assertEqual(completed.status_code, status.HTTP_200_OK)

        completed_bad = self.client.patch(f'/api/returns/{rid}/', data={'sale': self.other_sale.pk}, format='json')
        self.assertEqual(completed_bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(completed_bad.data.get('code'), 'RETURN_UPDATE_FORBIDDEN')

        completed_ok = self.client.patch(f'/api/returns/{rid}/', data={'comment': 'ok'}, format='json')
        self.assertEqual(completed_ok.status_code, status.HTTP_200_OK)

        canceled = self.client.post(f'/api/returns/{rid}/cancel/', data={}, format='json')
        self.assertEqual(canceled.status_code, status.HTTP_200_OK)

        canceled_edit = self.client.patch(f'/api/returns/{rid}/', data={'comment': 'x'}, format='json')
        self.assertEqual(canceled_edit.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(canceled_edit.data.get('code'), 'RETURN_UPDATE_FORBIDDEN')

    def test_complete_rules_and_effects(self):
        r = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '2', 'return_target': 'warehouse', 'condition_type': 'good'}])
        rid = r.data['id']
        before_qty = WarehouseBatch.objects.get(pk=self.batch.pk).quantity
        done = self.client.post(f'/api/returns/{rid}/complete/', data={}, format='json')
        self.assertEqual(done.status_code, status.HTTP_200_OK)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, WarehouseBatch.STATUS_AVAILABLE)
        self.assertGreater(self.batch.quantity, before_qty)

        repeat = self.client.patch(f'/api/returns/{rid}/complete/', data={}, format='json')
        self.assertEqual(repeat.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(repeat.data.get('code'), 'RETURN_ALREADY_COMPLETED')

        canceled = self.client.post(f'/api/returns/{rid}/cancel/', data={}, format='json')
        self.assertEqual(canceled.status_code, status.HTTP_200_OK)
        cant = self.client.post(f'/api/returns/{rid}/complete/', data={}, format='json')
        self.assertEqual(cant.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(cant.data.get('code'), 'RETURN_ALREADY_CANCELED')

        rd = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '1', 'return_target': 'defect', 'condition_type': 'defect'}])
        self.client.post(f'/api/returns/{rd.data["id"]}/complete/', data={}, format='json')
        self.assertTrue(DefectRecord.objects.filter(source_type=DefectRecord.SOURCE_RETURN).exists())

        rr = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '1', 'return_target': 'rework', 'condition_type': 'damaged'}])
        self.client.post(f'/api/returns/{rr.data["id"]}/complete/', data={}, format='json')
        self.assertTrue(ReworkRequest.objects.exists())

        retrieve = self.client.get(f'/api/returns/{rr.data["id"]}/')
        self.assertEqual(retrieve.status_code, status.HTTP_200_OK)
        self.assertIn('downstream_links', retrieve.data)

    def test_cancel_rules_and_locks(self):
        # draft cancel
        r1 = self._create_return()
        c1 = self.client.post(f'/api/returns/{r1.data["id"]}/cancel/', data={}, format='json')
        self.assertEqual(c1.status_code, status.HTTP_200_OK)
        self.assertEqual(c1.data['status'], Return.STATUS_CANCELED)

        # completed warehouse rollback
        r2 = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '1', 'return_target': 'warehouse', 'condition_type': 'good'}])
        rid2 = r2.data['id']
        self.client.post(f'/api/returns/{rid2}/complete/', data={}, format='json')
        c2 = self.client.post(f'/api/returns/{rid2}/cancel/', data={}, format='json')
        self.assertEqual(c2.status_code, status.HTTP_200_OK)

        # repeated cancel
        repeat = self.client.patch(f'/api/returns/{rid2}/cancel/', data={}, format='json')
        self.assertEqual(repeat.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(repeat.data.get('code'), 'RETURN_ALREADY_CANCELED')

        # lock by active refund
        r3 = self._create_return()
        rid3 = r3.data['id']
        self.client.post(f'/api/returns/{rid3}/complete/', data={}, format='json')
        Payment.objects.create(
            date=date(2026, 4, 26),
            client=self.client_active,
            linked_return_id=rid3,
            payment_type=Payment.TYPE_REFUND,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('10'),
            status=Payment.STATUS_ACTIVE,
        )
        locked = self.client.post(f'/api/returns/{rid3}/cancel/', data={}, format='json')
        self.assertEqual(locked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(locked.data.get('code'), 'REFUND_PAYMENT_EXISTS')

        # used defect lock
        r4 = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '1', 'return_target': 'defect', 'condition_type': 'defect'}])
        rid4 = r4.data['id']
        self.client.post(f'/api/returns/{rid4}/complete/', data={}, format='json')
        defect = DefectRecord.objects.filter(source_type=DefectRecord.SOURCE_RETURN).order_by('-id').first()
        defect.status = DefectRecord.STATUS_SOLD
        defect.save(update_fields=['status'])
        used = self.client.post(f'/api/returns/{rid4}/cancel/', data={}, format='json')
        self.assertEqual(used.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(used.data.get('code'), 'DOWNSTREAM_USED')

        # used rework lock
        r5 = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '1', 'return_target': 'rework', 'condition_type': 'damaged'}])
        rid5 = r5.data['id']
        self.client.post(f'/api/returns/{rid5}/complete/', data={}, format='json')
        rw = ReworkRequest.objects.order_by('-id').first()
        rw.status = ReworkRequest.STATUS_COMPLETED
        rw.save(update_fields=['status'])
        used_rw = self.client.post(f'/api/returns/{rid5}/cancel/', data={}, format='json')
        self.assertEqual(used_rw.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(used_rw.data.get('code'), 'DOWNSTREAM_USED')

        # negative warehouse rollback lock
        r6 = self._create_return(lines=[{'sale_line': self.line_shipped.pk, 'quantity': '2', 'return_target': 'warehouse', 'condition_type': 'good'}])
        rid6 = r6.data['id']
        self.client.post(f'/api/returns/{rid6}/complete/', data={}, format='json')
        self.batch.refresh_from_db()
        self.batch.quantity = Decimal('0.5')
        self.batch.save(update_fields=['quantity'])
        neg = self.client.post(f'/api/returns/{rid6}/cancel/', data={}, format='json')
        self.assertEqual(neg.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(neg.data.get('code'), 'WAREHOUSE_ROLLBACK_NEGATIVE')

    def test_select_sources_and_delete(self):
        # create fully returned line for exclusion
        full_return = self._create_return(
            sale=self.sale_closed.pk,
            lines=[{'sale_line': self.line_closed.pk, 'quantity': '8', 'return_target': 'warehouse', 'condition_type': 'good'}],
        )
        self.assertEqual(full_return.status_code, status.HTTP_201_CREATED)

        sources = self.client.get('/api/returns/select-sources/')
        self.assertEqual(sources.status_code, status.HTTP_200_OK)
        statuses = {x['sale_status'] for x in sources.data['sales']}
        self.assertTrue(statuses.issubset({Sale.STATUS_SHIPPED, Sale.STATUS_CLOSED}))

        sources_lines = self.client.get(f'/api/returns/select-sources/?sale_id={self.sale_closed.pk}')
        self.assertEqual(sources_lines.status_code, status.HTTP_200_OK)
        self.assertEqual(sources_lines.data['sale_lines'], [])

        sources_lines2 = self.client.get(f'/api/returns/select-sources/?sale_id={self.sale_shipped.pk}')
        self.assertEqual(sources_lines2.status_code, status.HTTP_200_OK)
        if sources_lines2.data['sale_lines']:
            row = sources_lines2.data['sale_lines'][0]
            self.assertIn('sold_quantity', row)
            self.assertIn('returned_quantity', row)
            self.assertIn('returnable_quantity', row)
            self.assertIn('unit_price', row)

        created = self._create_return()
        resp = self.client.delete(f'/api/returns/{created.data["id"]}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'DELETE_DISABLED')
