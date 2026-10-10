"""Staff-only pages for the front desk (the "Today" screen and its buttons).

Everything here requires a logged-in staff user. Anyone else is sent to the
admin login page and returned here afterwards.
"""
import datetime as dt
import re

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Appointment
from .site_views import label_for, shop_now

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


def row(appt):
    chip_class, chip_label = CHIPS[appt.status]
    can = {name: appt.status in spec[1] for name, spec in ACTIONS.items()}
    return {
        "obj": appt,
        "time": label_for(appt.time),
        "service": appt.get_service_display(),
        "phone": pretty_phone(appt.phone),
        "chip_class": chip_class,
        "chip_label": chip_label,
        "can": can,
        "texting_ok": appt.sms_consent,
    }


@staff_member_required
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
    return render(request, "core/staff/today.html", context)


@staff_member_required
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


@staff_member_required
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
