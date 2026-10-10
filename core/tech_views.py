"""Phone and iPad screens for the techs: today's jobs, scan a VIN, work order, add a finding.

These pages are big-button versions of the front desk screens and use the same data.
They require a logged-in staff user (give each tech an account with "Staff status").
"""
import re
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .admin_views import (
    LA, RS, LineForm, age_label, change_status, money, pretty_phone, report_uploads, save_uploads, totals,
)
from .models import LineItem, Part, RepairOrder, Vehicle

try:
    from .vin import decode_vin
except ImportError:  # pragma: no cover
    decode_vin = None

# What the tech sees for each order, most urgent first
STAGE_ORDER = {"in_progress": 0, "approved": 1, "estimate": 2, "complete": 3}


def stage(ro, lines):
    """(chip class, label) the tech sees for an order."""
    pending = sum(1 for li in lines if li.approval == LA.PENDING)
    if ro.status == RS.ESTIMATE:
        return ("wait", "Waiting on customer") if lines and pending else ("gray", "Needs inspection")
    if ro.status == RS.APPROVED:
        return ("info", "Approved, ready to start")
    if ro.status == RS.IN_PROGRESS:
        return ("info", "In progress")
    return ("ok", "Ready for pickup")


@staff_member_required
def jobs(request):
    qs = (
        RepairOrder.objects.exclude(status=RS.PAID)
        .select_related("vehicle__customer")
        .prefetch_related("line_items")
    )
    cards = []
    for ro in qs:
        lines = list(ro.line_items.all())
        chip_class, chip_label = stage(ro, lines)
        c = ro.vehicle.customer
        cards.append({
            "key": (STAGE_ORDER.get(ro.status, 9), ro.created_at),
            "url": reverse("tech_order", args=[ro.pk]),
            "vehicle": str(ro.vehicle) or "Vehicle",
            "who": f"{c.first_name} {c.last_name[:1]}.".strip() if c.last_name else c.first_name,
            "complaint": (ro.complaint or "").strip()[:90],
            "chip_class": chip_class,
            "chip_label": chip_label,
            "age": age_label(ro.created_at),
        })
    cards.sort(key=lambda c: c["key"])
    return render(request, "core/tech/jobs.html", {"cards": cards})


@staff_member_required
def scan(request):
    return render(request, "core/tech/scan.html", {})


def clean_vin(raw):
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


@staff_member_required
@require_http_methods(["GET", "POST"])
def vin(request):
    """After a VIN is scanned or typed: open the car's work order, start one, or send it to check in."""
    code = clean_vin(request.POST.get("vin") or request.GET.get("vin"))
    if len(code) != 17:
        messages.error(request, "A VIN has 17 characters. Try the photo again or type it in.")
        return redirect("tech_scan")

    vehicle = Vehicle.objects.filter(vin=code).select_related("customer").order_by("-id").first()

    if vehicle and request.method == "POST":
        ro = RepairOrder.objects.create(vehicle=vehicle)
        messages.success(request, f"Started a work order for {vehicle}.")
        return redirect("tech_order", pk=ro.pk)

    if vehicle:
        open_order = vehicle.repair_orders.exclude(status=RS.PAID).order_by("-created_at").first()
        if open_order:
            return redirect("tech_order", pk=open_order.pk)
        c = vehicle.customer
        return render(request, "core/tech/vin.html", {
            "state": "start", "vin": code, "vehicle": str(vehicle) or "Vehicle",
            "customer": f"{c.first_name} {c.last_name}".strip(),
        })

    # Not in the system yet. Show what the VIN decodes to and hand off to check in.
    info = None
    if decode_vin:
        try:
            info = decode_vin(code)
        except Exception:
            info = None
    query = {"vin": code}
    if info:
        query.update({k: info[k] for k in ("year", "make", "model") if info.get(k)})
    return render(request, "core/tech/vin.html", {
        "state": "unknown", "vin": code,
        "vehicle": " ".join(str(info[k]) for k in ("year", "make", "model") if info and info.get(k)),
        "checkin_url": reverse("staff_checkin") + "?" + urlencode(query),
    })


@staff_member_required
def order(request, pk):
    ro = get_object_or_404(RepairOrder.objects.select_related("vehicle__customer"), pk=pk)
    here = reverse("tech_order", args=[ro.pk])

    if request.method == "POST":
        do = request.POST.get("do")
        if do == "status" and request.POST.get("status") in (RS.IN_PROGRESS.value, RS.COMPLETE.value):
            change_status(request, ro, request.POST["status"])
        elif do == "photos":
            report_uploads(request, *save_uploads(request, ro, None))
        return redirect(here)

    lines = list(ro.line_items.select_related("part").order_by("id"))
    photo_count = {}
    video_count = {}
    for att in ro.attachments.all():
        bucket = video_count if att.is_video else photo_count
        bucket[att.line_item_id] = bucket.get(att.line_item_id, 0) + 1
    chips = {
        LA.PENDING.value: ("wait", "Awaiting approval"),
        LA.APPROVED.value: ("ok", "Approved"),
        LA.DECLINED.value: ("no", "Declined"),
    }
    rows = []
    for li in lines:
        p, v = photo_count.get(li.pk, 0), video_count.get(li.pk, 0)
        bits = []
        if p:
            bits.append(f"{p} photo{'s' if p != 1 else ''}")
        if v:
            bits.append(f"{v} video{'s' if v != 1 else ''}")
        chip_class, chip_label = chips[li.approval]
        rows.append({
            "name": str(li), "total": money(li.total), "media": " · ".join(bits),
            "chip_class": chip_class, "chip_label": chip_label, "declined": li.approval == LA.DECLINED,
        })
    general = photo_count.get(None, 0) + video_count.get(None, 0)
    sub, tax, total = totals(ro, lines)
    c = ro.vehicle.customer
    chip_class, chip_label = stage(ro, lines)
    return render(request, "core/tech/order.html", {
        "ro": ro, "vehicle": str(ro.vehicle) or "Vehicle", "v": ro.vehicle,
        "customer": f"{c.first_name} {c.last_name}".strip(), "phone": pretty_phone(c.phone),
        "chip_class": chip_class, "chip_label": chip_label,
        "rows": rows, "general": general, "total": money(total), "has_lines": bool(lines),
        "can_start": ro.status == RS.APPROVED,
        "can_finish": ro.status == RS.IN_PROGRESS,
    })


@staff_member_required
def finding(request, pk):
    ro = get_object_or_404(RepairOrder.objects.select_related("vehicle__customer"), pk=pk)
    form = None
    if request.method == "POST":
        form = LineForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            li = LineItem(
                repair_order=ro, kind=d["kind"], part=form.part, description=d["description"],
                quantity=d["quantity"],
                unit_price=d["unit_price"] if d["unit_price"] is not None else Decimal("0.00"),
            )
            li.save()
            if li.kind != LineItem.Kind.PART and li.total == 0:
                # a no-charge note ("tires look fine") has nothing for the customer to approve
                li.approval = LA.APPROVED
                li.save(update_fields=["approval"])
            report_uploads(request, *save_uploads(request, ro, li), quiet_if_empty=True)
            messages.success(request, f"Saved: {li}.")
            if form.part and form.part.quantity < d["quantity"]:
                messages.warning(request, f"Only {form.part.quantity:g} of {form.part.name} in stock.")
            return redirect("tech_order", pk=ro.pk)
        messages.error(request, "Not saved yet. Check the red messages.")
    return render(request, "core/tech/finding.html", {
        "ro": ro, "vehicle": str(ro.vehicle) or "Vehicle",
        "form": form or LineForm(initial={"kind": "labor", "quantity": 1}),
        "parts": [
            {"sku": p.sku, "label": f"{p.name} | {money(p.price)} | {p.quantity:g} in stock"}
            for p in Part.objects.all()[:500]
        ],
    })
