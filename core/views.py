import os

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from twilio.request_validator import RequestValidator

from .models import Attachment, Customer, Message, RepairOrder, Vehicle
from .vin import clean_vin, decode_vin

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB per file


@csrf_exempt
@require_POST
def twilio_incoming(request):
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(request.build_absolute_uri(), request.POST.dict(), signature):
        return HttpResponseForbidden()

    from_number = request.POST.get("From", "")
    body = request.POST.get("Body", "")
    customer = Customer.objects.filter(phone=from_number).first()

    Message.objects.create(
        customer=customer,
        phone=from_number,
        direction=Message.Direction.IN,
        body=body,
        twilio_sid=request.POST.get("MessageSid", ""),
    )

    if customer:
        word = body.strip().upper()
        if word in STOP_WORDS:
            customer.sms_opt_out = True
            customer.save()
        elif word in START_WORDS:
            customer.sms_opt_out = False
            customer.save()

    return HttpResponse("<Response></Response>", content_type="text/xml")


def estimate(request, token):
    ro = get_object_or_404(RepairOrder, approval_token=token)
    items = list(ro.line_items.prefetch_related("attachments"))
    can_respond = (
        ro.status == RepairOrder.Status.ESTIMATE
        and ro.responded_at is None
        and len(items) > 0
    )
    error = None

    if request.method == "POST" and can_respond:
        choices = {}
        for item in items:
            choice = request.POST.get(f"item_{item.id}")
            if choice not in ("approved", "declined"):
                error = "Please approve or decline every item before submitting."
                break
            choices[item.id] = choice

        if not error:
            for item in items:
                item.approval = choices[item.id]
                item.save(update_fields=["approval"])
            ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            ro.responded_at = timezone.now()
            ro.responded_ip = ip or request.META.get("REMOTE_ADDR") or None
            if "approved" in choices.values():
                ro.status = RepairOrder.Status.APPROVED
            ro.save()
            return redirect("estimate", token=token)

    return render(request, "core/estimate.html", {
        "ro": ro,
        "items": items,
        "general_media": ro.attachments.filter(line_item__isnull=True),
        "can_respond": can_respond,
        "error": error,
    })


@staff_member_required
def upload_media(request, pk):
    ro = get_object_or_404(RepairOrder, pk=pk)

    if request.method == "POST":
        raw_item = request.POST.get("line_item", "")
        line_item = ro.line_items.filter(pk=raw_item).first() if raw_item.isdigit() else None
        caption = request.POST.get("caption", "").strip()[:200]
        saved = 0
        for f in request.FILES.getlist("files"):
            ctype = f.content_type or ""
            if not (ctype.startswith("image/") or ctype.startswith("video/")):
                messages.error(request, f"{f.name}: only photos and videos are allowed.")
                continue
            if f.size > MAX_UPLOAD_BYTES:
                messages.error(request, f"{f.name}: file is too large (100 MB max).")
                continue
            Attachment.objects.create(
                repair_order=ro, line_item=line_item, file=f, caption=caption
            )
            saved += 1
        if saved:
            target = line_item.description if line_item else "General"
            messages.success(request, f"Uploaded {saved} file(s) to: {target}")
        url = reverse("upload_media", args=[ro.pk])
        return redirect(f"{url}?item={line_item.pk if line_item else ''}")

    return render(request, "core/upload_media.html", {
        "ro": ro,
        "line_items": ro.line_items.all(),
        "selected": request.GET.get("item", ""),
        "attachments": ro.attachments.select_related("line_item"),
    })


@staff_member_required
def scan_vin(request):
    if request.method == "POST":
        customer = get_object_or_404(Customer, pk=request.POST.get("customer"))
        vin = clean_vin(request.POST.get("vin", ""))
        if not vin:
            messages.error(
                request,
                "That doesn't look like a valid VIN. It must be 17 characters "
                "(letters and numbers, never I, O, or Q).",
            )
            return redirect(f"{reverse('scan_vin')}?customer={customer.pk}")
        vehicle = Vehicle.objects.create(customer=customer, vin=vin)
        if vehicle.make:
            messages.success(request, f"Saved {vehicle} for {customer}.")
        else:
            messages.warning(
                request,
                "Saved the vehicle, but the VIN lookup found no details. "
                "Please fill in year, make, and model.",
            )
        return redirect("admin:core_vehicle_change", vehicle.pk)

    return render(request, "core/scan_vin.html", {
        "customers": Customer.objects.all(),
        "selected": request.GET.get("customer", ""),
    })


@staff_member_required
def vin_lookup(request):
    vin = clean_vin(request.GET.get("vin", ""))
    info = decode_vin(vin) if vin else None
    if not info:
        return JsonResponse({"ok": False})
    return JsonResponse({"ok": True, **info})