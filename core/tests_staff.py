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
