from django.conf import settings
from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.html import format_html

from .models import Customer, LineItem, Message, RepairOrder, Vehicle
from .sms import send_sms


@admin.action(description="Send test text")
def send_test_text(modeladmin, request, queryset):
    for customer in queryset:
        try:
            send_sms(customer, "Test message from the shop.")
            modeladmin.message_user(request, f"Sent to {customer}")
        except Exception as e:
            modeladmin.message_user(
                request, f"Failed for {customer}: {e}", django_messages.ERROR
            )


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "phone", "sms_opt_out")
    search_fields = ("first_name", "last_name", "phone")
    inlines = [VehicleInline]
    actions = [send_test_text]


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
    readonly_fields = (
        "estimate_link",
        "responded_at",
        "responded_ip",
        "subtotal",
        "tax_amount",
        "total",
    )

    @admin.display(description="Customer estimate link")
    def estimate_link(self, obj):
        if not obj.pk:
            return "Save the repair order first"
        url = f"{settings.SITE_URL}/e/{obj.approval_token}/"
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "direction", "phone", "customer", "body")
    list_filter = ("direction",)