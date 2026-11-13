from .workspace import member_for


def navigation(request):
    if not request.user.is_authenticated:
        return {}
    member = getattr(request, "member", None)
    if member is None:
        member = member_for(request.user)
    return {"nav_member": member}
