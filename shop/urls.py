from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from core import views
from core import site_views
from core import admin_views, auth_views, tech_views, user_views


urlpatterns = [
    path("", site_views.home, name="home"),
    path("book/", site_views.book, name="book"),
    path("book/slots/", site_views.book_slots, name="book_slots"),
    path("book/thanks/", site_views.book_thanks, name="book_thanks"),
    path("signin/", auth_views.RoleLoginView.as_view(), name="role_login"),
    path("signout/", auth_views.RoleLogoutView.as_view(), name="role_logout"),
    path("go/", auth_views.go, name="role_home"),
    path("staff/users/", user_views.users, name="staff_users"),
    path("staff/", admin_views.today, name="staff_today"),
    path("staff/appointments/", admin_views.appointments, name="staff_appointments"),
    path("staff/appointments/<int:pk>/<str:action>/", admin_views.appointment_action, name="staff_appt_action"),
    path("staff/checkin/", admin_views.checkin, name="staff_checkin"),
    path("staff/checkin/<int:appt_id>/", admin_views.checkin, name="staff_checkin_appt"),
    path("staff/orders/", admin_views.orders, name="staff_orders"),
    path("staff/orders/<int:pk>/", admin_views.order, name="staff_order"),
    path("tech/", tech_views.jobs, name="tech_jobs"),
    path("tech/scan/", tech_views.scan, name="tech_scan"),
    path("tech/vin/", tech_views.vin, name="tech_vin"),
    path("tech/orders/<int:pk>/", tech_views.order, name="tech_order"),
    path("tech/orders/<int:pk>/finding/", tech_views.finding, name="tech_finding"),
    path("admin/", admin.site.urls),
    path("sms/incoming/", views.twilio_incoming),
    path("e/<str:token>/", views.estimate, name="estimate"),
    path("staff/ro/<int:pk>/media/", views.upload_media, name="upload_media"),
    path("staff/vin/", views.scan_vin, name="scan_vin"),
    path("staff/vin/lookup/", views.vin_lookup, name="vin_lookup"),
    path("privacy/", site_views.privacy, name="privacy"),
    path("sms-terms/", site_views.sms_terms, name="sms_terms")
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)