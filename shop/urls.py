from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from core import views
from core import site_views

urlpatterns = [
    path("", site_views.home, name="home"),
    path("book/", site_views.book, name="book"),
    path("book/slots/", site_views.book_slots, name="book_slots"),
    path("book/thanks/", site_views.book_thanks, name="book_thanks"),
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