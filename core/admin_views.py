"""Staff-only pages for the front desk (the "Today" screen and its buttons).

Everything here requires a logged-in staff user. Anyone else is sent to the
admin login page and returned here afterwards.
"""
import datetime as dt
import os
import re
from decimal import Decimal

from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import staff_screen
from .media import prepare_upload
from .models import Appointment, Attachment, Customer, LineItem, Part, RepairOrder, Vehicle
from .site_views import SHOP_TZ, label_for, normalize_phone, shop_now

A = Appointment

# action name -> (status it sets, statuses it may be applied from, message)
ACTIONS = {
    "confirm": (A.CONFIRMED, {A.REQUESTED}, "Confirmed {name}."),
    "arrived": (A.CHECKED_IN, {A.REQUESTED, A.CONFIRMED}, "{name} marked as arrived."),
    "no_show": (A.NO_SHOW, {A.REQUESTED, A.CONFIRMED}, "{name} marked as a no-show."),
    "cancel": (A.CANCELLED, {A.REQUESTED, A.CONFIRMED}, "Cancelled {name}."),
    "reopen": (A.REQUESTED, {A.CANCELLED, A.NO_SHOW}, "Put {name} back on the list."),
}

# status -> (chip colour class, label shown to staff)
CHIPS = {
    A.REQUESTED: ("wait", "Needs confirming"),
    A.CONFIRMED: ("info", "Confirmed"),
    A.CHECKED_IN: ("ok", "Arrived"),
    A.COMPLETED: ("ok", "Completed"),
    A.CANCELLED: ("no", "Cancelled"),
    A.NO_SHOW: ("no", "No-show"),
}


def pretty_phone(e164):
    digits = re.sub(r"\D", "", e164 or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return e164


def day_word(d, today):
    if d == today:
        return "Today"
    if d == today + dt.timedelta(days=1):
        return "Tomorrow"
    if d == today - dt.timedelta(days=1):
        return "Yesterday"
    return d.strftime("%A")


def ro_url(pk):
    """Where a repair order is worked on."""
    return reverse("staff_order", args=[pk])


def row(appt):
    chip_class, chip_label = CHIPS[appt.status]
    can = {name: appt.status in spec[1] for name, spec in ACTIONS.items()}
    can["checkin"] = appt.repair_order_id is None and appt.status in (A.REQUESTED, A.CONFIRMED, A.CHECKED_IN)
    return {
        "ro_link": ro_url(appt.repair_order_id) if appt.repair_order_id else "",
        "obj": appt,
        "time": label_for(appt.time),
        "service": appt.get_service_display(),
        "phone": pretty_phone(appt.phone),
        "chip_class": chip_class,
        "chip_label": chip_label,
        "can": can,
        "texting_ok": appt.sms_consent,
    }


def shop_tiles():
    """The "what is in the shop right now" numbers on the Today page."""
    open_orders = list(RepairOrder.objects.exclude(status=RepairOrder.Status.PAID).prefetch_related("line_items"))
    ready = [r for r in open_orders if r.status == RepairOrder.Status.COMPLETE]
    waiting = [
        r for r in open_orders
        if r.status != RepairOrder.Status.COMPLETE and any(li.approval == LineItem.Approval.PENDING for li in r.line_items.all())
    ]
    unpaid = sum((totals(r, list(r.line_items.all()))[2] for r in ready), Decimal("0.00"))
    return {
        "shop_cars": len(open_orders),
        "shop_waiting": len(waiting),
        "shop_ready": len(ready),
        "shop_unpaid": money(unpaid),
        "shop_unpaid_count": len(ready),
    }


@staff_screen
def today(request):
    now_date = shop_now().date()
    try:
        day = dt.date.fromisoformat(request.GET.get("d", ""))
    except ValueError:
        day = now_date

    visits = [row(a) for a in Appointment.objects.filter(date=day)]
    active = [r for r in visits if r["obj"].status not in (A.CANCELLED, A.NO_SHOW)]

    to_confirm = list(
        Appointment.objects.filter(status=A.REQUESTED, date__gte=now_date).order_by("date", "time")[:8]
    )
    to_confirm_rows = [
        {
            "obj": a,
            "when": f"{day_word(a.date, now_date)}, {a.date.strftime('%b')} {a.date.day} at {label_for(a.time)}",
            "service": a.get_service_display(),
            "phone": pretty_phone(a.phone),
            "link": f"?d={a.date.isoformat()}",
        }
        for a in to_confirm
    ]

    tomorrow = now_date + dt.timedelta(days=1)
    context = {
        "page": "today",
        "day": day,
        "day_title": day_word(day, now_date),
        # "Today" and "Tomorrow" need the weekday; "Thursday" already says it
        "day_long": (
            f"{day.strftime('%A, %B')} {day.day}, {day.year}"
            if day_word(day, now_date) in ("Today", "Tomorrow", "Yesterday")
            else f"{day.strftime('%B')} {day.day}, {day.year}"
        ),
        "is_today": day == now_date,
        "prev_day": (day - dt.timedelta(days=1)).isoformat(),
        "next_day": (day + dt.timedelta(days=1)).isoformat(),
        "visits": visits,
        "stat_visits": len(active),
        "stat_arrived": sum(1 for r in visits if r["obj"].status == A.CHECKED_IN),
        "stat_confirm": Appointment.objects.filter(status=A.REQUESTED, date__gte=now_date).count(),
        "stat_tomorrow": Appointment.objects.filter(date=tomorrow)
        .exclude(status__in=[A.CANCELLED, A.NO_SHOW])
        .count(),
        "to_confirm": to_confirm_rows,
    }
    context.update(shop_tiles())
    return render(request, "core/staff/today.html", context)


@staff_screen
@require_POST
def appointment_action(request, pk, action):
    spec = ACTIONS.get(action)
    appt = get_object_or_404(Appointment, pk=pk)
    back = f"{reverse('staff_today')}?d={appt.date.isoformat()}"
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = nxt
    if not spec:
        messages.error(request, "That button does not do anything.")
        return redirect(back)
    new_status, allowed_from, text = spec
    if appt.status not in allowed_from:
        messages.error(request, f"{appt.name} is already {appt.get_status_display().lower()}. Nothing changed.")
        return redirect(back)
    appt.status = new_status
    appt.save(update_fields=["status"])
    messages.success(request, text.format(name=appt.name))
    return redirect(back)


TABS = [
    ("upcoming", "Upcoming"),
    ("needs", "Needs confirming"),
    ("past", "Past"),
    ("cancelled", "Cancelled / no-show"),
    ("all", "All"),
]


@staff_screen
def appointments(request):
    now_date = shop_now().date()
    show = request.GET.get("show", "upcoming")
    if show not in dict(TABS):
        show = "upcoming"
    q = request.GET.get("q", "").strip()[:60]

    qs = Appointment.objects.all()
    inactive = [A.CANCELLED, A.NO_SHOW]
    if show == "upcoming":
        qs = qs.filter(date__gte=now_date).exclude(status__in=inactive)
    elif show == "needs":
        qs = qs.filter(status=A.REQUESTED, date__gte=now_date)
    elif show == "past":
        qs = qs.filter(date__lt=now_date).order_by("-date", "-time")
    elif show == "cancelled":
        qs = qs.filter(status__in=inactive).order_by("-date", "-time")
    else:
        qs = qs.order_by("-date", "-time")

    if q:
        match = Q(name__icontains=q) | Q(vehicle__icontains=q)
        digits = re.sub(r"\D", "", q)
        if digits:
            match |= Q(phone__contains=digits)
        qs = qs.filter(match)

    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    rows = []
    for a in page.object_list:
        r = row(a)
        r["day"] = f"{a.date.strftime('%a, %b')} {a.date.day}"
        r["day_link"] = f"{reverse('staff_today')}?d={a.date.isoformat()}"
        rows.append(r)

    context = {
        "page": "appointments",
        "tabs": [{"key": k, "label": label, "link": "?" + urlencode({"show": k, "q": q})} for k, label in TABS],
        "show": show,
        "q": q,
        "rows": rows,
        "pg": page,
        "pager_base": "?" + urlencode({"show": show, "q": q}) + "&page=",
        "next_url": request.get_full_path(),
    }
    return render(request, "core/staff/appointments.html", context)


# ---------------------------------------------------------------- check in

def split_name(full):
    parts = (full or "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def split_vehicle(text):
    """'2014 Honda Accord' -> (2014, 'Honda', 'Accord'). Staff can correct it on screen."""
    m = re.match(r"\s*(\d{4})\s+(\S+)\s*(.*)", text or "")
    if m:
        return int(m.group(1)), m.group(2), m.group(3).strip()
    parts = (text or "").split(None, 1)
    return None, (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


class CheckinForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100, error_messages={"required": "Add a last name so you can find them later."})
    phone = forms.CharField(max_length=30)
    email = forms.EmailField(required=False)
    sms_ok = forms.BooleanField(required=False)
    year = forms.IntegerField(required=False, min_value=1900, max_value=2100)
    make = forms.CharField(max_length=50, required=False)
    model = forms.CharField(max_length=50, required=False)
    vin = forms.CharField(max_length=30, required=False)
    license_plate = forms.CharField(max_length=15, required=False)
    mileage = forms.IntegerField(required=False, min_value=0, max_value=2_000_000)
    complaint = forms.CharField(required=False)

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get("phone"))

    def clean_vin(self):
        vin = re.sub(r"[^A-Za-z0-9]", "", self.cleaned_data.get("vin", "")).upper()
        if vin and len(vin) != 17:
            raise forms.ValidationError("A VIN has 17 characters. Leave it blank if you do not have it.")
        return vin

    def clean(self):
        data = super().clean()
        if not data.get("vin") and not (data.get("make") and data.get("model")):
            raise forms.ValidationError("Enter the VIN, or at least the make and model.")
        return data


def find_vehicle(customer, d):
    mine = customer.vehicles.all()
    if d["vin"]:
        hit = mine.filter(vin=d["vin"]).first()
        if hit:
            return hit
    elif d["make"] and d["model"]:
        hit = mine.filter(make__iexact=d["make"], model__iexact=d["model"]).filter(
            Q(year=d["year"]) | Q(year__isnull=True)
        ).first()
        if hit:
            return hit
    return None


@staff_screen
def checkin(request, appt_id=None):
    appt = get_object_or_404(Appointment, pk=appt_id) if appt_id else None

    if appt and appt.repair_order_id:
        messages.info(request, f"{appt.name} is already checked in.")
        return redirect(ro_url(appt.repair_order_id))
    if appt and appt.status in (A.CANCELLED, A.NO_SHOW, A.COMPLETED):
        messages.error(request, f"{appt.name}'s visit is {appt.get_status_display().lower()}. Use Undo on the appointment first.")
        return redirect(f"{reverse('staff_today')}?d={appt.date.isoformat()}")

    returning = None
    if request.method == "POST":
        form = CheckinForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            with transaction.atomic():
                customer = Customer.objects.filter(phone=d["phone"]).order_by("id").first()
                returning = customer
                if customer is None:
                    customer = Customer.objects.create(
                        first_name=d["first_name"], last_name=d["last_name"], phone=d["phone"],
                        email=d["email"], sms_opt_out=not d["sms_ok"],
                    )
                elif d["email"] and not customer.email:
                    customer.email = d["email"]
                    customer.save(update_fields=["email"])
                # A customer who texted STOP stays opted out, whatever this form says.

                vehicle = find_vehicle(customer, d)
                if vehicle is None:
                    vehicle = Vehicle.objects.create(
                        customer=customer, vin=d["vin"], year=d["year"], make=d["make"], model=d["model"],
                        license_plate=d["license_plate"], mileage=d["mileage"],
                    )
                else:
                    changed = []
                    if d["mileage"]:
                        vehicle.mileage = d["mileage"]; changed.append("mileage")
                    if d["license_plate"] and not vehicle.license_plate:
                        vehicle.license_plate = d["license_plate"]; changed.append("license_plate")
                    if changed:
                        vehicle.save(update_fields=changed)

                ro = RepairOrder.objects.create(vehicle=vehicle, complaint=d["complaint"])
                if appt:
                    appt.status = A.CHECKED_IN
                    appt.repair_order = ro
                    appt.save(update_fields=["status", "repair_order"])

            who = f"{customer} (already on file)" if returning else str(customer)
            messages.success(request, f"Checked in {vehicle} for {who}. Repair order #{ro.pk} is open.")
            return redirect(ro_url(ro.pk))
    else:
        initial = {}
        if not appt:
            initial = {k: request.GET[k] for k in ("vin", "year", "make", "model") if request.GET.get(k)}
        if appt:
            first, last = split_name(appt.name)
            year, make, model = split_vehicle(appt.vehicle)
            complaint = appt.get_service_display()
            if appt.notes:
                complaint += f": {appt.notes}"
            initial = {
                "first_name": first, "last_name": last, "phone": pretty_phone(appt.phone),
                "sms_ok": appt.sms_consent, "year": year, "make": make, "model": model,
                "vin": appt.vin, "complaint": complaint,
            }
            returning = Customer.objects.filter(phone=appt.phone).order_by("id").first()
            if returning:
                initial["first_name"], initial["last_name"] = returning.first_name, returning.last_name
                initial["email"] = returning.email
                initial["sms_ok"] = not returning.sms_opt_out and appt.sms_consent
        form = CheckinForm(initial=initial)

    context = {
        "page": "checkin",
        "form": form,
        "appt": appt,
        "returning": returning,
        "returning_vehicles": [str(v) for v in returning.vehicles.all()] if returning else [],
        "when": f"{appt.date.strftime('%A, %B')} {appt.date.day} at {label_for(appt.time)}" if appt else "",
    }
    return render(request, "core/staff/checkin.html", context)


# ----------------------------------------------------------- repair orders

R = RepairOrder
RS = RepairOrder.Status
LA = LineItem.Approval

ORDER_CHIPS = {
    RS.ESTIMATE.value: ("wait", "Estimate"),
    RS.APPROVED.value: ("info", "Approved"),
    RS.IN_PROGRESS.value: ("info", "In progress"),
    RS.COMPLETE.value: ("ok", "Ready for pickup"),
    RS.PAID.value: ("gray", "Paid"),
}
NEXT_STEP = {
    RS.ESTIMATE.value: (RS.APPROVED.value, "Mark approved"),
    RS.APPROVED.value: (RS.IN_PROGRESS.value, "Start work"),
    RS.IN_PROGRESS.value: (RS.COMPLETE.value, "Mark complete"),
    RS.COMPLETE.value: (RS.PAID.value, "Mark paid"),
}
LINE_CHIPS = {LA.PENDING.value: ("wait", "Waiting"), LA.APPROVED.value: ("ok", "Approved"), LA.DECLINED.value: ("no", "Declined")}
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")
MAX_UPLOAD = 250 * 1024 * 1024


def money(x):
    return f"${x:,.2f}"


def totals(ro, lines):
    """Same math as RepairOrder.total, but from lines already loaded."""
    sub = sum((li.total for li in lines if li.approval != LA.DECLINED), Decimal("0.00"))
    tax = (sub * ro.tax_rate / 100).quantize(Decimal("0.01"))
    return sub, tax, sub + tax


def age_label(created):
    days = (shop_now().date() - created.astimezone(SHOP_TZ).date()).days
    return "today" if days <= 0 else ("1 day" if days == 1 else f"{days} days")


def order_card(ro):
    lines = list(ro.line_items.all())
    _, _, total = totals(ro, lines)
    chip_class, chip_label = ORDER_CHIPS[ro.status]
    c = ro.vehicle.customer
    return {
        "obj": ro,
        "vehicle": str(ro.vehicle) or "Vehicle",
        "customer": f"{c.first_name} {c.last_name}".strip(),
        "complaint": (ro.complaint or "").strip()[:110],
        "total": money(total),
        "has_lines": bool(lines),
        "pending": sum(1 for li in lines if li.approval == LA.PENDING),
        "age": age_label(ro.created_at),
        "chip_class": chip_class,
        "chip_label": chip_label,
        "url": reverse("staff_order", args=[ro.pk]),
    }


@staff_screen
def orders(request):
    show = request.GET.get("show", "open")
    qs = R.objects.select_related("vehicle__customer").prefetch_related("line_items")
    if show == "paid":
        cards = [order_card(r) for r in qs.filter(status=RS.PAID)[:60]]
        columns = []
    else:
        show = "open"
        open_orders = [order_card(r) for r in qs.exclude(status=RS.PAID).order_by("created_at")]
        columns = [
            {"label": ORDER_CHIPS[st.value][1], "cards": [c for c in open_orders if c["obj"].status == st]}
            for st in (RS.ESTIMATE, RS.APPROVED, RS.IN_PROGRESS, RS.COMPLETE)
        ]
        cards = []
    return render(request, "core/staff/orders.html", {
        "page": "orders", "show": show, "columns": columns, "cards": cards,
        "open_count": R.objects.exclude(status=RS.PAID).count(),
    })


class LineForm(forms.Form):
    kind = forms.ChoiceField(choices=LineItem.Kind.choices)
    part_sku = forms.CharField(required=False, max_length=50)
    description = forms.CharField(required=False, max_length=200)
    quantity = forms.DecimalField(min_value=Decimal("0.01"), max_digits=8, decimal_places=2)
    unit_price = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=10, decimal_places=2)

    part = None

    def clean(self):
        data = super().clean()
        sku = (data.get("part_sku") or "").strip()
        if sku:
            self.part = Part.objects.filter(sku__iexact=sku).first()
            if not self.part:
                self.add_error("part_sku", "No part with that number. Pick one from the list, or leave it blank.")
        if not self.errors:
            if not self.part and not data.get("description"):
                raise forms.ValidationError("Describe the work, or pick a part.")
            if not self.part and data.get("unit_price") is None:
                self.add_error("unit_price", "Enter a price.")
        return data


class DetailsForm(forms.Form):
    complaint = forms.CharField(required=False)
    tax_rate = forms.DecimalField(min_value=Decimal("0"), max_value=Decimal("30"), max_digits=5, decimal_places=2)


def save_uploads(request, ro, line=None):
    """Save the photos/videos in request.FILES onto a repair order.

    HEIC photos are converted to JPEG. Returns (added, converted, skipped_reasons).
    """
    caption = request.POST.get("caption", "").strip()[:200]
    added, converted, skipped = 0, 0, []
    for f in request.FILES.getlist("files"):
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in PHOTO_EXTENSIONS + Attachment.VIDEO_EXTENSIONS:
            skipped.append(f"{f.name} (type not allowed)")
        elif f.size > MAX_UPLOAD:
            skipped.append(f"{f.name} (over 250 MB)")
        else:
            ready, why = prepare_upload(f)
            if ready is None:
                skipped.append(f"{f.name} ({why})")
                continue
            if ready is not f:
                converted += 1
            Attachment.objects.create(repair_order=ro, line_item=line, caption=caption, file=ready)
            added += 1
    return added, converted, skipped


def report_uploads(request, added, converted, skipped, quiet_if_empty=False):
    if added:
        extra = f" ({converted} iPhone HEIC photo(s) converted to JPEG)" if converted else ""
        messages.success(request, f"Added {added} photo/video file(s){extra}.")
    if skipped:
        messages.error(request, "Skipped: " + ", ".join(skipped))
    if not added and not skipped and not quiet_if_empty:
        messages.error(request, "Choose a photo or video first.")


def change_status(request, ro, new):
    """Move a repair order to a new status. Completing it takes parts out of stock, once."""
    if new not in RS.values or new == ro.status:
        return False
    ro.status = new
    ro.save(update_fields=["status", "updated_at"])
    if new == RS.COMPLETE:
        used = ro.deduct_stock()
        if used:
            messages.info(request, f"Took {used} part line(s) out of inventory.")
    messages.success(request, f"RO #{ro.pk} is now {ro.get_status_display().lower()}.")
    return True


@staff_screen
def order(request, pk):
    ro = get_object_or_404(R.objects.select_related("vehicle__customer"), pk=pk)
    here = reverse("staff_order", args=[ro.pk])
    line_form = None

    if request.method == "POST":
        do = request.POST.get("do", "")

        if do == "add_line":
            line_form = LineForm(request.POST)
            if line_form.is_valid():
                d = line_form.cleaned_data
                part = line_form.part
                li = LineItem(
                    repair_order=ro, kind=d["kind"], part=part, description=d["description"],
                    quantity=d["quantity"], unit_price=d["unit_price"] if d["unit_price"] is not None else Decimal("0.00"),
                )
                li.save()
                messages.success(request, f"Added {li}.")
                if part and part.quantity < d["quantity"]:
                    messages.warning(request, f"Heads up: only {part.quantity:g} of {part.name} in stock.")
                return redirect(here + "#lines")
            messages.error(request, "That line was not added. Check the red messages below.")

        elif do == "line":
            li = get_object_or_404(LineItem, pk=request.POST.get("line_id") or 0, repair_order=ro)
            act = request.POST.get("action")
            if act == "remove":
                name = str(li)
                li.delete()
                messages.success(request, f"Removed {name}.")
            elif act in (LA.APPROVED, LA.DECLINED, LA.PENDING):
                li.approval = act
                li.save(update_fields=["approval"])
                messages.success(request, f"{li} marked {act}.")
            return redirect(here + "#lines")

        elif do == "status":
            change_status(request, ro, request.POST.get("status"))
            return redirect(here)

        elif do == "details":
            f = DetailsForm(request.POST)
            if f.is_valid():
                ro.complaint = f.cleaned_data["complaint"]
                ro.tax_rate = f.cleaned_data["tax_rate"]
                ro.save(update_fields=["complaint", "tax_rate", "updated_at"])
                messages.success(request, "Saved.")
            else:
                messages.error(request, "Tax rate must be a number between 0 and 30, like 8.25.")
            return redirect(here)

        elif do == "upload":
            line = None
            if request.POST.get("line_item"):
                line = LineItem.objects.filter(pk=request.POST["line_item"], repair_order=ro).first()
            report_uploads(request, *save_uploads(request, ro, line))
            return redirect(here + "#photos")

        elif do == "delete_photo":
            att = get_object_or_404(Attachment, pk=request.POST.get("att_id") or 0, repair_order=ro)
            att.file.delete(save=False)
            att.delete()
            messages.success(request, "Removed.")
            return redirect(here + "#photos")

        else:
            return redirect(here)

    lines = list(ro.line_items.select_related("part").order_by("id"))
    sub, tax, total = totals(ro, lines)
    line_rows = []
    for li in lines:
        chip_class, chip_label = LINE_CHIPS[li.approval]
        line_rows.append({
            "obj": li, "name": str(li), "kind": li.get_kind_display(),
            "qty": f"{li.quantity:g}", "price": money(li.unit_price), "total": money(li.total),
            "chip_class": chip_class, "chip_label": chip_label,
            "declined": li.approval == LA.DECLINED,
        })
    line_names = {li.pk: str(li) for li in lines}
    files = []
    for att in ro.attachments.all():
        files.append({
            "obj": att, "url": att.file.url, "is_video": att.is_video,
            "caption": att.caption, "about": line_names.get(att.line_item_id, ""),
        })
    c = ro.vehicle.customer
    chip_class, chip_label = ORDER_CHIPS[ro.status]
    nxt = NEXT_STEP.get(ro.status)
    context = {
        "page": "orders",
        "ro": ro,
        "vehicle": str(ro.vehicle) or "Vehicle",
        "v": ro.vehicle,
        "customer": c,
        "customer_name": f"{c.first_name} {c.last_name}".strip(),
        "customer_phone": pretty_phone(c.phone),
        "texting_ok": not c.sms_opt_out,
        "chip_class": chip_class, "chip_label": chip_label,
        "next_status": nxt[0] if nxt else "", "next_label": nxt[1] if nxt else "",
        "statuses": [{"value": v, "label": label} for v, label in RS.choices],
        "lines": line_rows,
        "pending_count": sum(1 for li in lines if li.approval == LA.PENDING),
        "line_form": line_form or LineForm(initial={"kind": LineItem.Kind.LABOR, "quantity": 1}),
        "kinds": LineItem.Kind.choices,
        "parts": [
            {"sku": p.sku, "label": f"{p.name} | {money(p.price)} | {p.quantity:g} in stock"}
            for p in Part.objects.all()[:500]
        ],
        "subtotal": money(sub), "tax": money(tax), "total": money(total),
        "tax_rate": f"{ro.tax_rate:g}",
        "files": files,
        "line_choices": [{"id": li.pk, "name": str(li)} for li in lines],
        "estimate_url": request.build_absolute_uri(f"/e/{ro.approval_token}/"),
        "started": ro.created_at.astimezone(SHOP_TZ).strftime("%b ") + str(ro.created_at.astimezone(SHOP_TZ).day),
        "appointment": ro.appointments.first(),
    }
    return render(request, "core/staff/order.html", context)
