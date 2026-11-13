from .models import Membership


def navigation(request):
    if not request.user.is_authenticated:
        return {}
    member = getattr(request, "member", None)
    if member is None:
        member = (
            Membership.objects.select_related("company")
            .filter(user=request.user)
            .first()
        )
    return {"nav_member": member}
