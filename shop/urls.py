from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("sms/incoming/", views.twilio_incoming),
    path("e/<str:token>/", views.estimate, name="estimate"),
    path("staff/ro/<int:pk>/media/", views.upload_media, name="upload_media"),
    path("staff/vin/", views.scan_vin, name="scan_vin"),
    path("staff/vin/lookup/", views.vin_lookup, name="vin_lookup"),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)