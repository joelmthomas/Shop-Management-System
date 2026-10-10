import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .access import OWNER, STAFF, TECH, apply_role
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
        self.assertIn("signin", resp.url)

    def test_signed_in_without_a_role_is_refused(self):
        self.client.login(username="cust", password="pw")
        self.assertEqual(self.client.get(reverse("staff_today")).status_code, 403)
        self.assertEqual(self.act("confirm").status_code, 403)
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


class TechScreenTests(TestCase):
    def setUp(self):
        from .models import Customer, Part, RepairOrder, Vehicle
        User = get_user_model()
        apply_role(User.objects.create_user("tech", password="pw"), TECH)
        apply_role(User.objects.create_user("boss", password="pw"), OWNER)
        self.client.login(username="tech", password="pw")
        cust = Customer.objects.create(first_name="Jose", last_name="Alvarez", phone="+19725550143")
        self.vin = "1HGCR2F34EA000000"
        self.veh = Vehicle.objects.create(
            customer=cust, vin=self.vin, year=2014, make="Honda", model="Accord",
        )
        self.ro = RepairOrder.objects.create(vehicle=self.veh, complaint="Noise")
        self.part = Part.objects.create(sku="BP-1", name="Brake pads", quantity=5, price=64.99)

    def test_requires_staff(self):
        self.client.logout()
        for name, args in [("tech_jobs", []), ("tech_scan", []), ("tech_order", [self.ro.pk]), ("tech_finding", [self.ro.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302, name)

    def test_screens_load(self):
        self.assertContains(self.client.get(reverse("tech_jobs")), "Honda Accord")
        self.assertEqual(self.client.get(reverse("tech_scan")).status_code, 200)
        self.assertContains(self.client.get(reverse("tech_order", args=[self.ro.pk])), "Noise")
        self.assertEqual(self.client.get(reverse("tech_finding", args=[self.ro.pk])).status_code, 200)

    def test_vin_with_open_order_opens_it(self):
        resp = self.client.get(reverse("tech_vin") + "?vin=" + self.vin.lower())
        self.assertRedirects(resp, reverse("tech_order", args=[self.ro.pk]), fetch_redirect_response=False)

    def test_vin_without_open_order_can_start_one(self):
        from .models import RepairOrder
        self.ro.status = "paid"
        self.ro.save()
        page = self.client.get(reverse("tech_vin") + "?vin=" + self.vin)
        self.assertContains(page, "Start a work order")
        self.client.post(reverse("tech_vin"), {"vin": self.vin})
        self.assertEqual(RepairOrder.objects.exclude(status="paid").count(), 1)

    def test_unknown_vin_goes_to_check_in(self):
        from unittest import mock
        with mock.patch("core.tech_views.decode_vin", return_value={"year": 2020, "make": "Kia", "model": "Soul"}):
            resp = self.client.get(reverse("tech_vin") + "?vin=KNDJ23AU4L7000000")
        self.assertNotContains(resp, "make=Kia")  # a tech cannot open the front desk check in
        self.client.login(username="boss", password="pw")
        with mock.patch("core.tech_views.decode_vin", return_value={"year": 2020, "make": "Kia", "model": "Soul"}):
            resp = self.client.get(reverse("tech_vin") + "?vin=KNDJ23AU4L7000000")
        self.assertContains(resp, "Check this car in")
        self.assertContains(resp, "make=Kia")

    def test_short_vin_is_rejected(self):
        resp = self.client.get(reverse("tech_vin") + "?vin=123")
        self.assertRedirects(resp, reverse("tech_scan"), fetch_redirect_response=False)

    def test_check_in_form_is_prefilled_from_scan(self):
        self.client.login(username="boss", password="pw")
        resp = self.client.get(reverse("staff_checkin") + "?vin=KNDJ23AU4L7000000&make=Kia&model=Soul")
        self.assertContains(resp, 'value="Kia"')

    def test_add_labor_finding(self):
        self.client.post(reverse("tech_finding", args=[self.ro.pk]), {
            "kind": "labor", "description": "Brake job", "quantity": "1", "unit_price": "120",
        })
        li = self.ro.line_items.get()
        self.assertEqual(li.approval, "pending")

    def test_no_charge_finding_is_auto_approved(self):
        self.client.post(reverse("tech_finding", args=[self.ro.pk]), {
            "kind": "labor", "description": "Tires look fine", "quantity": "1", "unit_price": "0",
        })
        self.assertEqual(self.ro.line_items.get().approval, "approved")

    def test_part_finding_uses_inventory_price(self):
        self.client.post(reverse("tech_finding", args=[self.ro.pk]), {
            "kind": "part", "part_sku": "BP-1", "quantity": "2", "description": "",
        })
        li = self.ro.line_items.get()
        self.assertEqual(li.part, self.part)
        self.assertEqual(float(li.unit_price), 64.99)

    def test_bad_finding_is_not_saved(self):
        resp = self.client.post(reverse("tech_finding", args=[self.ro.pk]), {"kind": "labor", "quantity": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.ro.line_items.count(), 0)

    def test_tech_can_start_and_finish_but_not_mark_paid(self):
        url = reverse("tech_order", args=[self.ro.pk])
        self.client.post(url, {"do": "status", "status": "in_progress"})
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.status, "in_progress")
        self.client.post(url, {"do": "status", "status": "paid"})
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.status, "in_progress")
        self.client.post(url, {"do": "status", "status": "complete"})
        self.ro.refresh_from_db()
        self.assertEqual(self.ro.status, "complete")


class RoleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        apply_role(User.objects.create_user("boss", password="pw"), OWNER)
        for name, role in (("desk", STAFF), ("tech", TECH)):
            apply_role(User.objects.create_user(name, password="pw"), role)
        self.legacy = User.objects.create_user("old", password="pw", is_staff=True)
        self.nobody = User.objects.create_user("nobody", password="pw")

    def status(self, who, name):
        self.client.logout()
        self.client.login(username=who, password="pw")
        return self.client.get(reverse(name)).status_code

    def test_who_sees_what(self):
        table = {
            "boss": {"staff_today": 200, "tech_jobs": 200, "staff_users": 200},
            "desk": {"staff_today": 200, "tech_jobs": 403, "staff_users": 403},
            "old": {"staff_today": 200, "tech_jobs": 403, "staff_users": 403},
            "tech": {"staff_today": 403, "tech_jobs": 200, "staff_users": 403},
            "nobody": {"staff_today": 403, "tech_jobs": 403, "staff_users": 403},
        }
        for who, pages in table.items():
            for name, want in pages.items():
                self.assertEqual(self.status(who, name), want, f"{who} {name}")

    def test_signed_out_goes_to_signin(self):
        for name in ("staff_today", "tech_jobs", "staff_users"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/signin/", resp.url)

    def test_sign_in_lands_on_own_screen(self):
        for who, dest in (("desk", "staff_today"), ("tech", "tech_jobs"), ("boss", "staff_today")):
            self.client.logout()
            resp = self.client.post(reverse("role_login"), {"username": who, "password": "pw"})
            self.assertEqual(resp.status_code, 302, who)
            follow = self.client.get(resp.url)
            self.assertEqual(follow.status_code, 302, who)
            self.assertTrue(follow.url.endswith(reverse(dest)), who)

    def test_role_less_user_does_not_loop_on_signin_page(self):
        self.client.login(username="nobody", password="pw")
        self.assertEqual(self.client.get(reverse("role_login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("role_home")).status_code, 403)

    def test_apply_role_is_exclusive(self):
        u = self.legacy
        apply_role(u, TECH)
        apply_role(u, STAFF)
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Staff"])
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)


class UsersPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.User = User
        self.owner = User.objects.create_user("boss", password="pw")
        apply_role(self.owner, OWNER)
        self.client.login(username="boss", password="pw")
        self.url = reverse("staff_users")

    def post(self, **data):
        return self.client.post(self.url, data)

    def test_page_loads(self):
        self.assertContains(self.client.get(self.url), "Add a person")

    def test_add_user_with_role(self):
        resp = self.post(do="add", username="Mike", first_name="Mike", last_name="T", role=TECH,
                         password="Sturdy-Pass-4821", password2="Sturdy-Pass-4821")
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)
        u = self.User.objects.get(username="Mike")
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Tech"])
        self.assertTrue(u.check_password("Sturdy-Pass-4821"))
        self.assertFalse(u.is_staff)

    def test_add_rejects_duplicate_name_and_mismatch(self):
        resp = self.post(do="add", username="BOSS", role=TECH, password="Sturdy-Pass-4821", password2="Sturdy-Pass-4821")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.User.objects.count(), 1)
        resp = self.post(do="add", username="new", role=TECH, password="Sturdy-Pass-4821", password2="different")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.User.objects.count(), 1)

    def test_change_role(self):
        u = self.User.objects.create_user("desk", password="pw")
        apply_role(u, STAFF)
        self.post(do="role", user_id=u.pk, role=TECH)
        u.refresh_from_db()
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Tech"])
        self.post(do="role", user_id=u.pk, role="bogus")
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Tech"])

    def test_owner_cannot_demote_or_switch_off_self(self):
        self.post(do="role", user_id=self.owner.pk, role=TECH)
        self.post(do="active", user_id=self.owner.pk, active="0")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_superuser)
        self.assertTrue(self.owner.is_active)

    def test_last_owner_is_protected_but_another_owner_can_demote(self):
        second = self.User.objects.create_user("two", password="pw")
        apply_role(second, OWNER)
        self.post(do="role", user_id=second.pk, role=STAFF)
        second.refresh_from_db()
        self.assertFalse(second.is_superuser)

    def test_turn_off_blocks_sign_in(self):
        u = self.User.objects.create_user("desk", password="pw")
        apply_role(u, STAFF)
        self.post(do="active", user_id=u.pk, active="0")
        u.refresh_from_db()
        self.assertFalse(u.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username="desk", password="pw"))

    def test_reset_password(self):
        u = self.User.objects.create_user("desk", password="pw")
        apply_role(u, STAFF)
        self.post(do="password", user_id=u.pk, password="Sturdy-Pass-4821", password2="Sturdy-Pass-4821")
        u.refresh_from_db()
        self.assertTrue(u.check_password("Sturdy-Pass-4821"))
        self.post(do="password", user_id=u.pk, password="Another-Pass-9937", password2="nope")
        u.refresh_from_db()
        self.assertTrue(u.check_password("Sturdy-Pass-4821"))

    def test_staff_cannot_use_page(self):
        u = self.User.objects.create_user("desk", password="pw")
        apply_role(u, STAFF)
        self.client.logout()
        self.client.login(username="desk", password="pw")
        self.assertEqual(self.post(do="add", username="x", role=OWNER, password="Sturdy-Pass-4821", password2="Sturdy-Pass-4821").status_code, 403)
        self.assertFalse(self.User.objects.filter(username="x").exists())


class EstimatePageTests(TestCase):
    def setUp(self):
        from .models import Customer, LineItem, RepairOrder, Vehicle
        cust = Customer.objects.create(first_name="Jose", last_name="Alvarez", phone="+19725550143")
        veh = Vehicle.objects.create(customer=cust, year=2014, make="Honda", model="Accord")
        self.ro = RepairOrder.objects.create(vehicle=veh, complaint="Noise")
        self.a = LineItem.objects.create(repair_order=self.ro, kind="labor", description="Brake job", quantity=1, unit_price=120)
        self.b = LineItem.objects.create(repair_order=self.ro, kind="labor", description="Wiper blades", quantity=1, unit_price=20)
        self.url = reverse("estimate", args=[self.ro.approval_token])

    def test_customer_sees_branded_page_without_signing_in(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, "PHILIPS AUTOS")
        self.assertContains(resp, "Brake job")
        self.assertContains(resp, "Submit my choices")

    def test_every_line_must_be_answered(self):
        resp = self.client.post(self.url, {f"item_{self.a.id}": "approved"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "approve or decline every item")
        self.ro.refresh_from_db()
        self.assertIsNone(self.ro.responded_at)

    def test_answers_are_saved(self):
        resp = self.client.post(self.url, {f"item_{self.a.id}": "approved", f"item_{self.b.id}": "declined"})
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.ro.refresh_from_db()
        self.assertEqual((self.a.approval, self.b.approval), ("approved", "declined"))
        self.assertEqual(self.ro.status, "approved")
        self.assertContains(self.client.get(self.url), "Thank you")

    def test_tech_can_use_vin_lookup_but_signed_out_cannot(self):
        User = get_user_model()
        apply_role(User.objects.create_user("tech", password="pw"), TECH)
        self.assertEqual(self.client.get(reverse("vin_lookup")).status_code, 302)
        self.client.login(username="tech", password="pw")
        self.assertEqual(self.client.get(reverse("vin_lookup") + "?vin=123").status_code, 200)
