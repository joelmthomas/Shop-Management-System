import os

from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from twilio.request_validator import RequestValidator

from .models import Customer, Message

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}


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