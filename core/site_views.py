"""Public website: home page and online booking.

Shop rules live in the constants below, so they are easy to change.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

from django import forms
from django.db import transaction
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .models import Appointment

SHOP_TZ = ZoneInfo("America/Chicago")

# Weekday number (Mon=0) -> (opening hour, closing hour), 24-hour clock.
OPEN_HOURS = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 14)}
PER_SLOT = 2          # how many cars can be booked for the same hour
DAYS_AHEAD = 21       # how far ahead customers can book
LEAD_HOURS = 2        # no booking closer than this to "now"


def shop_now():
    return dt.datetime.now(SHOP_TZ).replace(tzinfo=None)


def open_days():
    """The dates customers can pick, starting today."""
    today = shop_now().date()
    days = []
    for i in range(DAYS_AHEAD + 1):
        d = today + dt.timedelta(days=i)
        if d.weekday() in OPEN_HOURS and slots_for(d, only_available=True):
            days.append(d)
    return days


def label_for(t):
    hour12 = t.hour % 12 or 12
    return f"{hour12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def slots_for(d, only_available=False):
    """Hourly start times for a date, each flagged available or full."""
    if d.weekday() not in OPEN_HOURS:
        return []
    start, end = OPEN_HOURS[d.weekday()]
    cutoff = shop_now() + dt.timedelta(hours=LEAD_HOURS)
    booked = {
        row["time"]: row["n"]
        for row in Appointment.objects.filter(date=d)
        .exclude(status=Appointment.CANCELLED)
        .values("time")
        .annotate(n=Count("id"))
    }
    out = []
    for hour in range(start, end):
        t = dt.time(hour, 0)
        if dt.datetime.combine(d, t) < cutoff:
            continue
        free = booked.get(t, 0) < PER_SLOT
        if only_available and not free:
            continue
        out.append({"value": t.strftime("%H:%M"), "label": label_for(t), "available": free})
    return out


def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise forms.ValidationError("Enter a 10-digit mobile number, like (972) 555-0100.")
    return "+1" + digits


class BookingForm(forms.ModelForm):
    website = forms.CharField(required=False)  # honeypot: real people leave it empty

    class Meta:
        model = Appointment
        fields = ["service", "date", "time", "name", "phone", "vehicle", "vin", "notes", "sms_consent"]

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get("phone"))

    def clean_vin(self):
        return re.sub(r"[^A-Za-z0-9]", "", self.cleaned_data.get("vin", "")).upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise forms.ValidationError("Something went wrong. Please call the shop to book.")
        d, t = cleaned.get("date"), cleaned.get("time")
        if d and t:
            match = [s for s in slots_for(d) if s["value"] == t.strftime("%H:%M")]
            if not match or not match[0]["available"]:
                raise forms.ValidationError("That time was just taken. Please pick another one.")
        return cleaned


def flag_strips():
    """Vertical slices of the logo's two flags. CSS waves each slice a little
    differently, so the flags on the shield ripple."""
    def build(x0, x1, inner_is_right, phase):
        width = 6
        out = []
        for i in range((x1 - x0) // width):
            x = x0 + i * width
            distance = (x1 - (x + width)) / (x1 - x0) if inner_is_right else (x - x0) / (x1 - x0)
            out.append({
                "x": x,
                "amp": round(8 * max(0, distance) ** 1.1, 2),
                "delay": round(i * 0.17 + phase, 2),
            })
        return out

    return build(55, 139, True, 0) + build(256, 346, False, 0.9)


SERVICES_SHOWN = [
    ("mechanical", "Mechanical repair", "Timing belts, shocks and struts, and more."),
    ("maintenance", "Maintenance", "Routine service to keep your car running the way it should."),
    ("body", "Body work", "Get your car looking right again."),
    ("tires", "Tires", "New and used tires."),
    ("detail", "Detailing & hand wash", "Inside and out."),
]


def home(request):
    return render(request, "core/site/home.html", {"strips": flag_strips(), "services": SERVICES_SHOWN})


def book(request):
    days = open_days()
    if request.method == "POST":
        with transaction.atomic():
            # Validate and save together so the "is this hour still free?" check
            # runs right before the booking is written.
            form = BookingForm(request.POST)
            if form.is_valid():
                appt = form.save(commit=False)
                appt.status = Appointment.REQUESTED
                appt.save()
                request.session["last_appointment"] = appt.pk
                return redirect("book_thanks")
    else:
        form = BookingForm(initial={"service": request.GET.get("service", ""), "sms_consent": True})
    return render(request, "core/site/book.html", {"form": form, "days": days, "services": Appointment.SERVICE_CHOICES})


@require_GET
def book_slots(request):
    try:
        d = dt.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        raise Http404
    today = shop_now().date()
    if d < today or d > today + dt.timedelta(days=DAYS_AHEAD):
        raise Http404
    return JsonResponse({"slots": slots_for(d)})

def privacy(request):
    return render(request, "core/site/privacy.html")


def sms_terms(request):
    return render(request, "core/site/sms_terms.html")

    
def book_thanks(request):
    appt = None
    pk = request.session.get("last_appointment")
    if pk:
        appt = Appointment.objects.filter(pk=pk).first()
    if not appt:
        return redirect("book")
    return render(request, "core/site/thanks.html", {"appt": appt, "time_label": label_for(appt.time)})
