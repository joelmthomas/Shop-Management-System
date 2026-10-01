import os
import secrets
import uuid
from decimal import Decimal

from django.db import models, transaction
from django.db.models import F

from .vin import decode_vin


def generate_token():
    return secrets.token_urlsafe(32)


class Customer(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, help_text="Mobile, e.g. +15551234567")
    email = models.EmailField(blank=True)
    sms_opt_out = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Vehicle(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="vehicles"
    )
    vin = models.CharField(max_length=17, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    make = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=50, blank=True)
    license_plate = models.CharField(max_length=15, blank=True)
    mileage = models.PositiveIntegerField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.vin:
            self.vin = self.vin.strip().upper()
            if not (self.year and self.make and self.model):
                info = decode_vin(self.vin)
                if info:
                    self.year = self.year or info["year"]
                    self.make = self.make or info["make"]
                    self.model = self.model or info["model"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.year or ''} {self.make} {self.model}".strip()


class Part(models.Model):
    sku = models.CharField("Part number / SKU", max_length=50, unique=True)
    name = models.CharField(max_length=200)
    supplier = models.CharField(
        max_length=100, blank=True, help_text="Where you buy it, e.g. NAPA (optional)"
    )
    quantity = models.DecimalField(
        "In stock", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="After the part is created, change this by adding stock movements.",
    )
    reorder_level = models.DecimalField(
        "Reorder at", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="You'll see a low-stock flag when stock is at or below this.",
    )
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    location = models.CharField("Shelf / bin", max_length=50, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "inventory item"
        verbose_name_plural = "inventory"

    @property
    def is_low(self):
        return self.quantity <= self.reorder_level

    def __str__(self):
        return f"{self.sku} - {self.name}"


class StockMovement(models.Model):
    class Reason(models.TextChoices):
        RECEIVED = "received", "Received from vendor"
        USED = "used", "Used on repair order"
        ADJUSTMENT = "adjustment", "Count correction"
        RETURN = "return", "Returned"

    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="movements")
    change = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Use a positive number to add stock and a negative number to remove it.",
    )
    reason = models.CharField(
        max_length=20, choices=Reason.choices, default=Reason.RECEIVED
    )
    repair_order = models.ForeignKey(
        "RepairOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stock_movements",
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        with transaction.atomic():
            super().save(*args, **kwargs)
            if is_new:
                Part.objects.filter(pk=self.part_id).update(
                    quantity=F("quantity") + self.change
                )

    def __str__(self):
        return f"{self.part.sku} {self.change:+}"


class RepairOrder(models.Model):
    class Status(models.TextChoices):
        ESTIMATE = "estimate", "Estimate"
        APPROVED = "approved", "Approved"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETE = "complete", "Complete"
        PAID = "paid", "Paid"

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="repair_orders"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ESTIMATE
    )
    complaint = models.TextField(blank=True, help_text="Customer's concern")
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"),
        help_text="Percent, e.g. 8.25",
    )
    # Used later for the customer approval link
    approval_token = models.CharField(
        max_length=64, unique=True, default=generate_token, editable=False
    )
    responded_at = models.DateTimeField(null=True, blank=True, editable=False)
    responded_ip = models.GenericIPAddressField(null=True, blank=True, editable=False)
    stock_deducted = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RO #{self.pk} - {self.vehicle}"

    @property
    def subtotal(self):
        lines = self.line_items.exclude(approval=LineItem.Approval.DECLINED)
        return sum((li.total for li in lines), Decimal("0.00"))

    @property
    def tax_amount(self):
        return (self.subtotal * self.tax_rate / 100).quantize(Decimal("0.01"))

    @property
    def total(self):
        return self.subtotal + self.tax_amount

    def deduct_stock(self):
        """Take parts used on this repair order out of inventory, once.

        Declined lines are skipped. Safe to call more than once.
        """
        if self.stock_deducted:
            return 0
        used = 0
        with transaction.atomic():
            lines = (
                self.line_items.filter(part__isnull=False)
                .exclude(approval=LineItem.Approval.DECLINED)
                .select_related("part")
            )
            for li in lines:
                StockMovement.objects.create(
                    part=li.part,
                    change=-li.quantity,
                    reason=StockMovement.Reason.USED,
                    repair_order=self,
                    note=f"RO #{self.pk}",
                )
                used += 1
            self.stock_deducted = True
            self.save(update_fields=["stock_deducted"])
        return used


class LineItem(models.Model):
    class Kind(models.TextChoices):
        LABOR = "labor", "Labor"
        PART = "part", "Part"
        FEE = "fee", "Fee"

    class Approval(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.CASCADE, related_name="line_items"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LABOR)
    part = models.ForeignKey(
        Part, on_delete=models.PROTECT, null=True, blank=True, related_name="line_items",
        help_text="Optional. Picking a part fills in the name and price and tracks stock.",
    )
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1.00"))
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    approval = models.CharField(
        max_length=10, choices=Approval.choices, default=Approval.PENDING
    )

    def save(self, *args, **kwargs):
        if self.part_id:
            if not self.description:
                self.description = self.part.name
            if not self.unit_price:
                self.unit_price = self.part.price
            self.kind = self.Kind.PART
        super().save(*args, **kwargs)

    @property
    def total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return self.description or (self.part.name if self.part_id else "Line item")


class Message(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Inbound"
        OUT = "out", "Outbound"

    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages"
    )
    phone = models.CharField(max_length=20)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    body = models.TextField()
    twilio_sid = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.direction} {self.phone}: {self.body[:40]}"


def attachment_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"repair_orders/{instance.repair_order_id}/{uuid.uuid4().hex}{ext}"


class Attachment(models.Model):
    VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")

    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.CASCADE, related_name="attachments"
    )
    line_item = models.ForeignKey(
        LineItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attachments",
        help_text="Leave blank for general photos of the vehicle",
    )
    file = models.FileField(upload_to=attachment_path)
    caption = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    @property
    def is_video(self):
        return self.file.name.lower().endswith(self.VIDEO_EXTENSIONS)

    def __str__(self):
        return self.caption or self.file.name