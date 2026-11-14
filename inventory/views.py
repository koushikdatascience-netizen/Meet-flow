import csv
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ItemForm, UserForm, CategoryForm
from .models import (
    Company,
    Entry,
    Item,
    ItemCategory,
    Line,
    Membership,
    post_entry,
    reverse_entry,
    edit_stock_entry,
)
from .workspace import member_for


def staff_access(role=None):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            member = member_for(request.user)
            if not member or not member.company.active:
                raise PermissionDenied(
                    "No active staff access. Contact your administrator."
                )
            request.member = member
            if member.must_change_password:
                return redirect("password_change")
            if role and member.role not in role:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorate


def page(request, template, **context):
    context.setdefault(
        "page_name",
        {
            "report": "Reports",
            "users": "Users",
            "items": "Items",
            "categories": "Item Categories",
            "entries": context.get("title", "Stock entries"),
            "home": "Home",
            "delete": "Delete entry",
            "receipt": "Stock document",
        }.get(template, "Workspace"),
    )
    return render(request, "inventory/" + template + ".html", context)


@login_required
def home(request):
    member = member_for(request.user)
    if member:
        if not member.company.active:
            raise PermissionDenied(
                "The workspace is inactive. Contact your administrator."
            )
        request.member = member
        if member.must_change_password:
            return redirect("password_change")
        return page(
            request,
            "home",
            item_count=Item.objects.filter(company=member.company, active=True).count(),
            today=timezone.localdate(),
            recent=Entry.objects.filter(
                company=member.company, kind__in=member.allowed_features
            ).select_related("created_by")[:6],
        )
    raise PermissionDenied("No features assigned. Contact your administrator.")


@staff_access(["admin"])
def items(request):
    category = request.GET.get("category", "")
    if not ItemCategory.objects.filter(
        company=request.member.company, name=category, active=True
    ).exists():
        category = ""
    item = (
        get_object_or_404(Item, pk=request.GET["edit"], company=request.member.company)
        if request.GET.get("edit")
        else None
    )
    form = ItemForm(
        request.POST or None,
        instance=item,
        initial={"category": category} if category and not item else None,
        company=request.member.company,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=request.member.company_id)
                obj = form.save(commit=False)
                obj.company = request.member.company
                if not ItemCategory.objects.filter(
                    company=obj.company, name=obj.category, active=True
                ).exists():
                    raise ValidationError("Choose an available category.")
                if (
                    item
                    and Line.objects.filter(item=item).exists()
                    and Item.objects.get(pk=item.pk).unit != obj.unit
                ):
                    raise ValidationError("Unit cannot change after stock entries.")
                obj.save()
            return redirect("stock_items")
        except (IntegrityError, ValidationError) as exc:
            form.add_error(
                None,
                "Item name already exists." if isinstance(exc, IntegrityError) else exc,
            )
    return page(
        request,
        "items",
        form=form,
        items=Item.objects.filter(
            company=request.member.company,
            **({"category": category} if category else {}),
        ),
        selected_category=category,
    )


@staff_access(["admin"])
def categories(request):
    editing = (
        get_object_or_404(
            ItemCategory,
            pk=request.GET["edit"],
            company=request.member.company,
            active=True,
        )
        if request.GET.get("edit")
        else None
    )
    form = CategoryForm(
        request.POST or None, initial={"name": editing.name} if editing else None
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=request.member.company_id)
                name = form.cleaned_data["name"]
                duplicate = ItemCategory.objects.filter(
                    company=request.member.company, name__iexact=name
                )
                if editing:
                    duplicate = duplicate.exclude(pk=editing.pk)
                if duplicate.exists():
                    raise ValidationError("That category already exists.")
                if editing:
                    current = ItemCategory.objects.get(pk=editing.pk, active=True)
                    Item.objects.filter(
                        company=request.member.company, category=current.name
                    ).update(category=name)
                    current.name = name
                    current.save(update_fields=["name"])
                else:
                    ItemCategory.objects.create(
                        company=request.member.company, name=name
                    )
            messages.success(request, "Saved.")
            return redirect("stock_categories")
        except (IntegrityError, ValidationError) as exc:
            form.add_error(
                "name",
                "That category already exists."
                if isinstance(exc, IntegrityError)
                else exc,
            )
    counts = dict(
        Item.objects.filter(company=request.member.company, active=True)
        .values("category")
        .annotate(count=Count("pk"))
        .values_list("category", "count")
    )
    return page(
        request,
        "categories",
        categories=[
            {
                "pk": category.pk,
                "name": category.name,
                "count": counts.get(category.name, 0),
            }
            for category in ItemCategory.objects.filter(
                company=request.member.company, active=True
            )
        ],
        form=form,
        editing=editing,
    )


@staff_access(["admin"])
def remove_category(request, pk):
    category = get_object_or_404(
        ItemCategory, pk=pk, company=request.member.company, active=True
    )
    count = Item.objects.filter(
        company=request.member.company, category=category.name
    ).count()
    if request.method == "POST":
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=request.member.company_id)
            category = get_object_or_404(
                ItemCategory, pk=pk, company=request.member.company, active=True
            )
            if Item.objects.filter(
                company=request.member.company, category=category.name
            ).exists():
                messages.error(request, "Move the items to another category first.")
            else:
                category.delete()
                messages.success(request, "Deleted.")
                return redirect("stock_categories")
    return page(request, "remove_category", category=category, item_count=count)


@staff_access(["admin"])
def remove_item(request, pk):
    item = get_object_or_404(Item, pk=pk, company=request.member.company)
    if request.method == "POST":
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=request.member.company_id)
            item.active = False
            item.save(update_fields=["active"])
        messages.success(
            request,
            "Item removed from new entries. Its stock history is preserved; restore it using Edit.",
        )
        return redirect("stock_items")
    return page(request, "remove_item", item=item)


@staff_access(["admin", "manager", "worker", "support", "cook", "server", "operator"])
def entries(request, kind):
    if kind not in dict(Entry.KINDS):
        raise PermissionDenied
    if kind not in request.member.allowed_features:
        raise PermissionDenied
    company = request.member.company
    editing = None
    if request.GET.get("edit"):
        if "edit" not in request.member.allowed_features:
            raise PermissionDenied
        editing = get_object_or_404(
            Entry.objects.prefetch_related("lines__item"),
            pk=request.GET["edit"],
            company=company,
            kind=kind,
            reversed_at__isnull=True,
        )
    tokens = request.session.get("stock_tokens", [])
    if request.method == "POST":
        try:
            token = request.POST.get("token", "")
            if token not in tokens:
                raise ValidationError("Form expired. Reload and try again.")
            ids = request.POST.getlist("item")
            quantities = request.POST.getlist("quantity")
            if len(ids) != len(quantities) or len(ids) > 100:
                raise ValidationError("Invalid item rows.")
            if any(
                not q.isascii() or not q.isdigit()
                for pk, q in zip(ids, quantities)
                if pk or q
            ):
                raise ValidationError(
                    "Enter whole numbers of pieces only, such as 1, 2 or 10. Decimals are not allowed."
                )
            rows = [(int(pk), Decimal(q)) for pk, q in zip(ids, quantities) if pk or q]
            if any(
                not q.is_finite()
                or q.as_tuple().exponent < -3
                or q > Decimal("9999999999.999")
                for _, q in rows
            ):
                raise ValidationError("Enter a valid whole-number quantity of pieces.")
            if editing:
                edit_stock_entry(
                    company,
                    request.user,
                    editing.pk,
                    uuid.UUID(token),
                    int(request.POST.get("version", "-1")),
                    rows,
                )
            else:
                post_entry(
                    company,
                    request.user,
                    kind,
                    timezone.localdate(),
                    "",
                    uuid.UUID(token),
                    rows,
                )
            messages.success(request, "Saved.")
            return redirect("stock_entries", kind=kind)
        except (
            ValueError,
            InvalidOperation,
            KeyError,
            Item.DoesNotExist,
            ValidationError,
        ) as exc:
            messages.error(
                request,
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Check the date, items and quantities.",
            )
    token = str(uuid.uuid4())
    request.session["stock_tokens"] = (tokens + [token])[-30:]
    available = list(
        Item.objects.filter(company=company).filter(
            Q(active=True)
            | Q(
                pk__in=[line.item_id for line in editing.lines.all()] if editing else []
            )
        )
    )
    names = {str(item.pk): item.name for item in available}
    initial_rows = (
        [
            {"id": pk, "name": names.get(pk, ""), "quantity": quantity}
            for pk, quantity in zip(
                request.POST.getlist("item"), request.POST.getlist("quantity")
            )
        ]
        if request.method == "POST"
        else [
            {
                "id": str(line.item_id),
                "name": line.item.name,
                "quantity": format(line.quantity, "f").rstrip("0").rstrip(".")
                if "." in format(line.quantity, "f")
                else str(line.quantity),
            }
            for line in editing.lines.all()
        ]
        if editing
        else []
    )
    return page(
        request,
        "entries",
        kind=kind,
        editing=editing,
        title=dict(Entry.KINDS)[kind],
        token=token,
        today=editing.date if editing else timezone.localdate(),
        initial_rows=initial_rows,
        items=available,
        entries=Paginator(
            Entry.objects.filter(company=company, kind=kind).prefetch_related(
                "lines__item", "reversed_by"
            ),
            25,
        ).get_page(request.GET.get("page")),
    )


@staff_access(["admin", "manager", "worker", "support", "cook", "server", "operator"])
def delete_entry(request, pk):
    if "delete" not in request.member.allowed_features:
        raise PermissionDenied
    entry = get_object_or_404(Entry, pk=pk, company=request.member.company)
    if entry.kind not in request.member.allowed_features:
        raise PermissionDenied
    if request.method == "POST":
        try:
            reverse_entry(
                request.member.company, request.user, pk, "Deleted by user confirmation"
            )
            messages.success(request, "Entry deleted by reversal. Stock recalculated.")
            return redirect("stock_entries", kind=entry.kind)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return page(request, "delete", entry=entry)


@staff_access(["admin", "manager", "worker", "support", "cook", "server", "operator"])
def receipt(request, pk):
    entry = get_object_or_404(
        Entry.objects.prefetch_related("lines__item"),
        pk=pk,
        company=request.member.company,
    )
    if entry.kind not in request.member.allowed_features:
        raise PermissionDenied
    return page(request, "receipt", entry=entry)


@staff_access(["admin"])
def users(request):
    member = (
        get_object_or_404(
            Membership, pk=request.GET["edit"], company=request.member.company
        )
        if request.GET.get("edit")
        else None
    )
    if member and member.user.is_superuser and not request.user.is_superuser:
        raise PermissionDenied
    initial = (
        {
            "username": member.user.username,
            "role": member.role,
            "active": member.user.is_active,
            "features": member.allowed_features,
        }
        if member
        else {"features": ["reports"]}
    )
    form = UserForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=request.member.company_id)
                data = form.cleaned_data
                if (
                    member
                    and member.user_id == request.user.pk
                    and (data["role"] != "admin" or not data["active"])
                ):
                    raise ValidationError(
                        "You cannot remove your own administrator access."
                    )
                if not member and not data["temporary_password"]:
                    raise ValidationError(
                        "Enter a temporary password for the new user."
                    )
                user = member.user if member else get_user_model()()
                user.username = data["username"]
                user.is_active = data["active"]
                if data["temporary_password"]:
                    user.set_password(data["temporary_password"])
                user.save()
                obj = member or Membership(company=request.member.company, user=user)
                obj.role = data["role"]
                obj.features = data["features"] if data["role"] != "admin" else None
                if data["temporary_password"]:
                    obj.must_change_password = True
                obj.save()
            return redirect("stock_users")
        except (IntegrityError, ValidationError) as exc:
            form.add_error(
                None,
                "Username is already taken."
                if isinstance(exc, IntegrityError)
                else exc,
            )
    return page(
        request,
        "users",
        form=form,
        users=Membership.objects.filter(company=request.member.company).select_related(
            "user"
        ),
    )


@staff_access()
def report(request):
    if "reports" not in request.member.allowed_features:
        raise PermissionDenied
    today = timezone.localdate()
    start = end = today
    category = request.GET.get("category", "")
    items = Item.objects.filter(company=request.member.company)
    categories = list(
        ItemCategory.objects.filter(
            company=request.member.company, active=True
        ).values_list("name", flat=True)
    )
    if category and category not in categories:
        category = ""
        messages.error(request, "Choose an available category.")
    if category:
        items = items.filter(category=category)
    item_list = list(items)
    movements = list(
        Line.objects.filter(
            item__in=item_list, entry__reversed_at__isnull=True, entry__date__lte=end
        ).select_related("entry", "item")
    )
    balances = {item.pk: Decimal("0") for item in item_list}
    daily = {}
    for line in movements:
        delta = -line.quantity if line.entry.kind == "sale" else line.quantity
        if line.entry.date < start:
            balances[line.item_id] += delta
        else:
            key = (line.entry.date, line.item_id, line.entry.kind)
            daily[key] = daily.get(key, Decimal("0")) + line.quantity
    rows = []
    day = start
    while day <= end:
        for item in item_list:
            added = daily.get((day, item.pk, "opening"), Decimal("0"))
            purchased = daily.get((day, item.pk, "purchase"), Decimal("0"))
            sold = daily.get((day, item.pk, "sale"), Decimal("0"))
            opening = balances[item.pk] + added
            total = opening + purchased
            closing = total - sold
            rows.append(
                [
                    day,
                    item.name,
                    opening,
                    purchased,
                    total,
                    sold,
                    closing,
                ]
            )
            balances[item.pk] = closing
        day += timedelta(days=1)
    headers = [
        "Date",
        "Item",
        "Opening stock",
        "Purchase",
        "Total",
        "Sale",
        "Closing stock",
    ]
    totals = [sum((row[index] for row in rows), Decimal(0)) for index in range(2, 7)]
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="stock-report.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [
                    "'" + value
                    if isinstance(value, str)
                    and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                    for value in row
                ]
            )
        writer.writerow(["", "Total", *totals])
        return response
    return page(
        request,
        "report",
        rows=rows,
        headers=headers,
        start=start,
        end=end,
        categories=categories,
        category=category,
        totals=totals,
    )
