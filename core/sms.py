import os

from twilio.rest import Client

from .models import Message


def send_sms(customer, body):
    if customer.sms_opt_out:
        raise ValueError("Customer has opted out of texts")
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    sent = client.messages.create(
        to=customer.phone,
        from_=os.environ["TWILIO_PHONE_NUMBER"],
        body=body,
    )
    return Message.objects.create(
        customer=customer,
        phone=customer.phone,
        direction=Message.Direction.OUT,
        body=body,
        twilio_sid=sent.sid,
    )