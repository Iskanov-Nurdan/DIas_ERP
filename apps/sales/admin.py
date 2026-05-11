from django.contrib import admin
from .models import Client, Sale, Shipment


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact', 'phone', 'phone_alt', 'client_type', 'inn')


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'client', 'warehouse_batch', 'product', 'quantity', 'price', 'date')
    list_filter = ('client',)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale', 'quantity', 'status', 'shipment_date', 'delivery_date')
    list_filter = ('status',)
