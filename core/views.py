import os

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from twilio.request_validator import RequestValidator

from .models import Attachment, Customer, Message, RepairOrder

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
    items = list(ro.line_items.all())
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
        "can_respond": can_respond,
        "error": error,
    })


def estimate_media(request, token):
    ro = get_object_or_404(RepairOrder, approval_token=token)
    return render(request, "core/estimate_media.html", {
        "ro": ro,
        "attachments": ro.attachments.all(),
    })


@staff_member_required
def upload_media(request, pk):
    ro = get_object_or_404(RepairOrder, pk=pk)

    if request.method == "POST":
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
            Attachment.objects.create(repair_order=ro, file=f, caption=caption)
            saved += 1
        if saved:
            messages.success(request, f"Uploaded {saved} file(s).")
        return redirect("upload_media", pk=ro.pk)

    return render(request, "core/upload_media.html", {
        "ro": ro,
        "attachments": ro.attachments.all(),
    })