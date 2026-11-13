from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class LegacyAdminGuard:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            if not request.user.is_authenticated:
                return redirect("login")
            raise PermissionDenied(
                "The former billing admin is closed. Use the stock management pages."
            )
        return self.get_response(request)
