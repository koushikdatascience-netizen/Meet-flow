"""Resolve the single internal workspace; staff cannot select another company."""

from .models import Company, Membership


def member_for(user):
    company = Company.objects.filter(is_primary=True).first()
    if not company:
        return None
    member = (
        Membership.objects.select_related("company")
        .filter(user=user, company=company)
        .first()
    )
    if user.is_superuser and not member:
        member, _ = Membership.objects.get_or_create(
            user=user,
            defaults={
                "company": company,
                "role": "admin",
                "must_change_password": False,
            },
        )
        if member.company_id != company.pk:
            return None
    return member
