from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from shop.views import (
    invoice_view,
    reports_view,
    billing_view,
    return_receipt_view,
    health_view,
    create_return_view,
    purchase_entry_view,
    purchase_receipt_view,
    home_view,
    items_view,
    item_form_view,
    item_remove_view,
    item_restore_view,
)

urlpatterns = [
    path("items/", items_view, name="items"),
    path("items/new/", item_form_view, name="item_add"),
    path("items/<int:pk>/edit/", item_form_view, name="item_edit"),
    path("items/<int:pk>/delete/", item_remove_view, name="item_remove"),
    path("items/<int:pk>/restore/", item_restore_view, name="item_restore"),
    path("purchases/new/", purchase_entry_view, name="purchase_entry"),
    path("purchases/<int:pk>/", purchase_receipt_view, name="purchase_receipt"),
    path("return/<str:kind>/<int:pk>/", create_return_view, name="create_return"),
    path("healthz/", health_view, name="health"),
    path("pos/", billing_view, name="billing"),
    path("returns/<int:pk>/", return_receipt_view, name="return_receipt"),
    path("", home_view, name="home"),
    path("admin/", admin.site.urls),
    path("invoice/<int:pk>/", invoice_view, name="invoice"),
    path("reports/", reports_view, name="reports"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
