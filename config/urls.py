from django.contrib.auth import views as auth
from django.urls import path

from inventory import views
from inventory.auth import password_help
from shop.views import health_view

urlpatterns = [
    path("", views.home, name="home"),
    path(
        "login/",
        auth.LoginView.as_view(
            template_name="inventory/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/", password_help, name="password_change"),
    path("items/", views.items, name="stock_items"),
    path("categories/", views.categories, name="stock_categories"),
    path(
        "categories/<int:pk>/delete/",
        views.remove_category,
        name="stock_category_remove",
    ),
    path("items/<int:pk>/remove/", views.remove_item, name="stock_item_remove"),
    path("users/", views.users, name="stock_users"),
    path("clear-stock/", views.clear_stock_view, name="clear_stock"),
    path("entries/<str:kind>/", views.entries, name="stock_entries"),
    path("entries/<int:pk>/delete/", views.delete_entry, name="stock_delete"),
    path("document/<int:pk>/", views.receipt, name="stock_receipt"),
    path("reports/", views.report, name="stock_report"),
    path("healthz/", health_view, name="health"),
]
