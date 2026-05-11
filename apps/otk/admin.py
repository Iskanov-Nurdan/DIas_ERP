from django.contrib import admin
from .models import OtkCheck


@admin.register(OtkCheck)
class OtkCheckAdmin(admin.ModelAdmin):
    list_display = ('batch', 'accepted', 'rejected', 'reject_reason', 'inspector', 'checked_date')
    list_filter = ('inspector',)
