from django.contrib import admin

from .models import Customer, LineItem, RepairOrder, Vehicle


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "phone", "sms_opt_out")
    search_fields = ("first_name", "last_name", "phone")
    inlines = [VehicleInline]


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "customer", "vin", "license_plate")
    search_fields = ("vin", "license_plate", "make", "model")


class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 1


@admin.register(RepairOrder)
class RepairOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle", "status", "created_at")
    list_filter = ("status",)
    inlines = [LineItemInline]
    readonly_fields = ("approval_token", "subtotal", "tax_amount", "total")