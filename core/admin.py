from django.conf import settings
from django.contrib import admin
from django.contrib import messages as django_messages
from django.db.models import F
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    Attachment, Customer, LineItem, Message, Part, RepairOrder,
    StockMovement, Vehicle,
)
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
    readonly_fields = ("scan_vin_link",)

    @admin.display(description="Add vehicle by VIN photo")
    def scan_vin_link(self, obj):
        if not obj.pk:
            return "Save the customer first"
        url = f"{reverse('scan_vin')}?customer={obj.pk}"
        return format_html('<a class="button" href="{}">Scan a VIN</a>', url)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "customer", "vin", "license_plate")
    search_fields = ("vin", "license_plate", "make", "model")


# ---------- Inventory ----------  (shows as one searchable "Inventory" list)

class LowStockFilter(admin.SimpleListFilter):
    title = "stock level"
    parameter_name = "stock"

    def lookups(self, request, model_admin):
        return [("low", "Low or out of stock")]

    def queryset(self, request, queryset):
        if self.value() == "low":
            return queryset.filter(quantity__lte=F("reorder_level"))
        return queryset


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 1
    fields = ("change", "reason", "note", "repair_order", "created_at")
    readonly_fields = ("repair_order", "created_at")

    def has_change_permission(self, request, obj=None):
        return False  # history is add-only

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    list_display = (
        "sku", "name", "quantity", "reorder_level", "low_stock",
        "cost", "price", "supplier", "location",
    )
    list_filter = (LowStockFilter,)
    search_fields = ("sku", "name", "supplier", "location", "notes")
    inlines = [StockMovementInline]

    @admin.display(description="Low?", boolean=True)
    def low_stock(self, obj):
        return obj.is_low

    def get_readonly_fields(self, request, obj=None):
        # After creation, stock only changes through movements so the history stays accurate.
        return ("quantity",) if obj else ()


# ---------- Repair orders ----------

class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 1
    autocomplete_fields = ["part"]


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "line_item":
            object_id = request.resolver_match.kwargs.get("object_id")
            if object_id:
                kwargs["queryset"] = LineItem.objects.filter(repair_order_id=object_id)
            else:
                kwargs["queryset"] = LineItem.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(RepairOrder)
class RepairOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle", "status", "created_at")
    list_filter = ("status",)
    inlines = [LineItemInline, AttachmentInline]
    readonly_fields = (
        "estimate_link",
        "upload_link",
        "responded_at",
        "responded_ip",
        "stock_deducted",
        "subtotal",
        "tax_amount",
        "total",
    )

    def save_related(self, request, form, formsets, change):
        # Line items are saved after the repair order itself, so deduct stock here.
        super().save_related(request, form, formsets, change)
        ro = form.instance
        if ro.status in (RepairOrder.Status.COMPLETE, RepairOrder.Status.PAID):
            used = ro.deduct_stock()
            if used:
                self.message_user(
                    request, f"Removed {used} part line(s) from inventory."
                )

    @admin.display(description="Customer estimate link")
    def estimate_link(self, obj):
        if not obj.pk:
            return "Save the repair order first"
        url = f"{settings.SITE_URL}/e/{obj.approval_token}/"
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Photos / video")
    def upload_link(self, obj):
        if not obj.pk:
            return "Save the repair order first"
        url = reverse("upload_media", args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Take or upload photos / video</a>', url
        )


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "direction", "phone", "customer", "body")
    list_filter = ("direction",)