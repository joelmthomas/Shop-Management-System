import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Appointment


class StaffTodayTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user("desk", password="pw", is_staff=True)
        self.visitor = User.objects.create_user("cust", password="pw")
        self.appt = Appointment.objects.create(
            service="maintenance", date=dt.date.today() + dt.timedelta(days=1), time=dt.time(10, 0),
            name="Test Customer", phone="+19725550100", vehicle="2014 Honda Accord",
        )

    def act(self, action):
        return self.client.post(reverse("staff_appt_action", args=[self.appt.pk, action]))

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(reverse("staff_today"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_non_staff_is_sent_to_login(self):
        self.client.login(username="cust", password="pw")
        self.assertEqual(self.client.get(reverse("staff_today")).status_code, 302)
        self.assertEqual(self.act("confirm").status_code, 302)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.REQUESTED)

    def test_staff_sees_page(self):
        self.client.login(username="desk", password="pw")
        resp = self.client.get(reverse("staff_today") + "?d=" + self.appt.date.isoformat())
        self.assertContains(resp, "Test Customer")

    def test_bad_date_falls_back_to_today(self):
        self.client.login(username="desk", password="pw")
        self.assertEqual(self.client.get(reverse("staff_today") + "?d=nonsense").status_code, 200)

    def test_confirm_then_cancel_then_undo(self):
        self.client.login(username="desk", password="pw")
        self.act("confirm")
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.CONFIRMED)
        self.act("cancel")
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.CANCELLED)
        self.act("reopen")
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.REQUESTED)

    def test_cannot_confirm_a_cancelled_visit(self):
        self.client.login(username="desk", password="pw")
        self.appt.status = Appointment.CANCELLED
        self.appt.save()
        self.act("confirm")
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.CANCELLED)

    def test_get_request_does_nothing(self):
        self.client.login(username="desk", password="pw")
        resp = self.client.get(reverse("staff_appt_action", args=[self.appt.pk, "confirm"]))
        self.assertEqual(resp.status_code, 405)


class StaffAppointmentsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_user("desk", password="pw", is_staff=True)
        self.appt = Appointment.objects.create(
            service="tires", date=dt.date.today() + dt.timedelta(days=2), time=dt.time(11, 0),
            name="Pat Searchable", phone="+19725550177", vehicle="2020 Kia Soul",
        )

    def test_requires_staff(self):
        self.assertEqual(self.client.get(reverse("staff_appointments")).status_code, 302)

    def test_lists_search_by_name_and_phone(self):
        self.client.login(username="desk", password="pw")
        url = reverse("staff_appointments")
        self.assertContains(self.client.get(url), "Pat Searchable")
        self.assertContains(self.client.get(url + "?q=searchable"), "Pat Searchable")
        self.assertContains(self.client.get(url + "?q=(972) 555-0177"), "Pat Searchable")
        self.assertNotContains(self.client.get(url + "?q=zzzz"), "Pat Searchable")

    def test_unknown_tab_falls_back(self):
        self.client.login(username="desk", password="pw")
        self.assertEqual(self.client.get(reverse("staff_appointments") + "?show=bogus").status_code, 200)

    def test_action_returns_to_list_but_not_to_other_sites(self):
        self.client.login(username="desk", password="pw")
        url = reverse("staff_appt_action", args=[self.appt.pk, "confirm"])
        resp = self.client.post(url, {"next": "/staff/appointments/?show=needs"})
        self.assertEqual(resp.url, "/staff/appointments/?show=needs")
        self.appt.status = Appointment.REQUESTED
        self.appt.save()
        resp = self.client.post(url, {"next": "https://evil.example/"})
        self.assertTrue(resp.url.startswith("/staff/?d="))


class StaffCheckinTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_user("desk", password="pw", is_staff=True)
        self.client.login(username="desk", password="pw")
        self.appt = Appointment.objects.create(
            service="mechanical", date=dt.date.today(), time=dt.time(10, 0),
            name="Jose Alvarez", phone="+19725550143", vehicle="2014 Honda Accord",
            notes="Grinding noise", sms_consent=True,
        )
        self.data = {
            "first_name": "Jose", "last_name": "Alvarez", "phone": "(972) 555-0143",
            "year": "2014", "make": "Honda", "model": "Accord", "complaint": "Grinding noise",
            "sms_ok": "on",
        }

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("staff_checkin")).status_code, 302)

    def test_form_prefills_from_appointment(self):
        resp = self.client.get(reverse("staff_checkin_appt", args=[self.appt.pk]))
        self.assertContains(resp, 'value="Jose"')
        self.assertContains(resp, 'value="Honda"')

    def test_appointment_checkin_creates_everything(self):
        from .models import Customer, RepairOrder, Vehicle
        self.client.post(reverse("staff_checkin_appt", args=[self.appt.pk]), self.data)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.CHECKED_IN)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(RepairOrder.objects.count(), 1)
        self.assertEqual(self.appt.repair_order.complaint, "Grinding noise")
        self.assertFalse(Customer.objects.get().sms_opt_out)

    def test_second_checkin_does_not_duplicate(self):
        from .models import RepairOrder
        url = reverse("staff_checkin_appt", args=[self.appt.pk])
        self.client.post(url, self.data)
        self.client.post(url, self.data)
        self.assertEqual(RepairOrder.objects.count(), 1)

    def test_walk_in_reuses_customer_and_vehicle_by_phone(self):
        from .models import Customer, RepairOrder, Vehicle
        url = reverse("staff_checkin")
        self.client.post(url, self.data)
        self.client.post(url, self.data)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(RepairOrder.objects.count(), 2)

    def test_no_text_consent_is_stored(self):
        from .models import Customer
        data = dict(self.data)
        data.pop("sms_ok")
        self.client.post(reverse("staff_checkin"), data)
        self.assertTrue(Customer.objects.get().sms_opt_out)

    def test_existing_opt_out_is_not_overridden(self):
        from .models import Customer
        Customer.objects.create(first_name="Jose", last_name="Alvarez", phone="+19725550143", sms_opt_out=True)
        self.client.post(reverse("staff_checkin"), self.data)
        self.assertTrue(Customer.objects.get().sms_opt_out)

    def test_bad_input_creates_nothing(self):
        from .models import Customer
        data = dict(self.data, phone="555", make="", model="")
        resp = self.client.post(reverse("staff_checkin"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Customer.objects.count(), 0)

    def test_cancelled_visit_cannot_be_checked_in(self):
        self.appt.status = Appointment.CANCELLED
        self.appt.save()
        resp = self.client.post(reverse("staff_checkin_appt", args=[self.appt.pk]), self.data)
        self.assertEqual(resp.status_code, 302)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.CANCELLED)


class StaffOrderTests(TestCase):
    def setUp(self):
        from .models import Customer, Part, RepairOrder, Vehicle
        User = get_user_model()
        User.objects.create_user("desk", password="pw", is_staff=True)
        self.client.login(username="desk", password="pw")
        cust = Customer.objects.create(first_name="Jose", last_name="Alvarez", phone="+19725550143")
        veh = Vehicle.objects.create(customer=cust, year=2014, make="Honda", model="Accord")
        self.ro = RepairOrder.objects.create(vehicle=veh, complaint="Noise")
        self.part = Part.objects.create(sku="BP-1", name="Brake pads", quantity=5, price=64.99)
        self.url = reverse("staff_order", args=[self.ro.pk])

    def post(self, **data):
        return self.client.post(self.url, data)

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(reverse("staff_orders")).status_code, 302)

    def test_board_and_detail_load(self):
        self.assertContains(self.client.get(reverse("staff_orders")), "Honda Accord")
        self.assertContains(self.client.get(self.url), "Honda Accord")
        self.assertEqual(self.client.get(reverse("staff_orders") + "?show=paid").status_code, 200)

    def test_today_shows_shop_tiles(self):
        self.assertContains(self.client.get(reverse("staff_today")), "Cars in the shop")

    def test_add_labor_line(self):
        self.post(do="add_line", kind="labor", description="Brake job", quantity="1", unit_price="120")
        li = self.ro.line_items.get()
        self.assertEqual(li.description, "Brake job")
        self.assertEqual(li.approval, "pending")

    def test_add_part_line_fills_name_and_price(self):
        self.post(do="add_line", kind="part", part_sku="bp-1", quantity="2")
        li = self.ro.line_items.get()
        self.assertEqual(li.part, self.part)
        self.assertEqual(li.description, "Brake pads")
        self.assertEqual(float(li.unit_price), 64.99)

    def test_unknown_part_and_empty_line_are_rejected(self):
        self.assertEqual(self.post(do="add_line", kind="part", part_sku="NOPE", quantity="1").status_code, 200)
        self.assertEqual(self.post(do="add_line", kind="labor", quantity="1", unit_price="10").status_code, 200)
        self.assertEqual(self.ro.line_items.count(), 0)

    def test_approve_decline_remove(self):
        self.post(do="add_line", kind="labor", description="Job", quantity="1", unit_price="100")
        li = self.ro.line_items.get()
        self.post(do="line", line_id=li.pk, action="approved")
        li.refresh_from_db()
        self.assertEqual(li.approval, "approved")
        self.post(do="line", line_id=li.pk, action="declined")
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.total, 0)
        self.post(do="line", line_id=li.pk, action="remove")
        self.assertEqual(self.ro.line_items.count(), 0)

    def test_complete_takes_parts_out_once(self):
        self.post(do="add_line", kind="part", part_sku="BP-1", quantity="2")
        self.post(do="status", status="complete")
        self.post(do="status", status="in_progress")
        self.post(do="status", status="complete")
        self.part.refresh_from_db()
        self.assertEqual(float(self.part.quantity), 3.0)

    def test_save_details_and_bad_tax(self):
        self.post(do="details", complaint="New concern", tax_rate="8.25")
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.complaint, "New concern")
        self.assertEqual(float(self.ro.tax_rate), 8.25)
        self.post(do="details", complaint="x", tax_rate="abc")
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.complaint, "New concern")

    def test_disallowed_upload_type_is_skipped(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad = SimpleUploadedFile("virus.exe", b"x")
        self.client.post(self.url, {"do": "upload", "files": bad})
        self.assertEqual(self.ro.attachments.count(), 0)


class MediaTests(TestCase):
    def test_heic_becomes_jpeg_and_other_files_pass_through(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .media import prepare_upload

        buf = io.BytesIO()
        Image.new("RGBA", (40, 30), (200, 30, 30, 255)).save(buf, "PNG")
        # Pillow reads by content, so a PNG named .HEIC exercises the same conversion path
        fake_heic = SimpleUploadedFile("IMG_0001.HEIC", buf.getvalue())
        out, why = prepare_upload(fake_heic)
        self.assertIsNone(why)
        self.assertEqual(out.name, "IMG_0001.jpg")
        self.assertEqual(out.read()[:3], b"\xff\xd8\xff")

        jpg = SimpleUploadedFile("a.jpg", b"abc")
        self.assertIs(prepare_upload(jpg)[0], jpg)

        junk = SimpleUploadedFile("b.heic", b"not an image")
        self.assertIsNone(prepare_upload(junk)[0])
