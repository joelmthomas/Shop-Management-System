import os
import secrets
import uuid
from decimal import Decimal

from django.db import models

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
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1.00"))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    approval = models.CharField(
        max_length=10, choices=Approval.choices, default=Approval.PENDING
    )

    @property
    def total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return self.description

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