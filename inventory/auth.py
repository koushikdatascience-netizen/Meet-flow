from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy

from .models import Membership


class ChangePasswordView(PasswordChangeView):
    template_name = "inventory/password.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        result = super().form_valid(form)
        Membership.objects.filter(user=self.request.user).update(
            must_change_password=False
        )
        return result
