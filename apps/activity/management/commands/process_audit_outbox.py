"""Повторная запись событий из очереди audit_outbox в user_activity."""

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.models import AuditOutbox, UserActivity


class Command(BaseCommand):
    help = 'Обработать очередь audit_outbox (после сбоев записи аудита).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=500,
            help='Максимум строк за один запуск',
        )

    def handle(self, *args, **options):
        limit = max(1, options['limit'])
        qs = AuditOutbox.objects.filter(processed_at__isnull=True).order_by('pk')[:limit]
        ok = err = dup = 0
        for row in qs:
            with transaction.atomic():
                row.attempts = (row.attempts or 0) + 1
                try:
                    UserActivity.objects.create(**row.payload)
                    row.processed_at = timezone.now()
                    row.save(update_fields=['processed_at', 'attempts'])
                    ok += 1
                except IntegrityError:
                    row.processed_at = timezone.now()
                    row.save(update_fields=['processed_at', 'attempts'])
                    dup += 1
                except Exception as exc:
                    row.last_error = str(exc)[:2000]
                    row.save(update_fields=['attempts', 'last_error'])
                    err += 1
        self.stdout.write(
            self.style.SUCCESS(f'audit outbox: written={ok} duplicate_skip={dup} errors={err}')
        )
