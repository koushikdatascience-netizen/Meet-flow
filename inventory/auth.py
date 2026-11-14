from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .workspace import member_for


@login_required
def password_help(request):
    # Passwords are set by administrators in Users, including for existing accounts.
    member = member_for(request.user)
    return redirect("stock_users" if member and member.role == "admin" else "home")
