from decimal import Decimal
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .models import Sale, Purchase, Product


@staff_member_required
def invoice_view(request, pk):
    if not request.user.has_perm("shop.view_sale"):
        raise PermissionDenied
    sale = get_object_or_404(
        Sale.objects.prefetch_related("lines__product"), pk=pk, posted=True
    )
    return render(
        request,
        "shop/invoice.html",
        {
            "sale": sale,
            "thermal": request.GET.get("paper") == "thermal",
            "auto_print": request.GET.get("print") == "1",
        },
    )


@staff_member_required
def reports_view(request):
    if not request.user.has_perm("shop.view_reports"):
        raise PermissionDenied
    from datetime import date, timedelta
    from urllib.parse import urlencode
    from django.core.paginator import Paginator
    from .reporting import REPORT_TABS, build_report, select_rows, metrics, report_csv

    today = timezone.localdate()
    ranges = {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "week": (today - timedelta(days=6), today),
        "month": (today.replace(day=1), today),
    }
    period = request.GET.get("period", "today")
    preset_start, preset_end = ranges.get(period, ranges["today"])
    date_error = ""
    try:
        start = date.fromisoformat(request.GET.get("start", str(preset_start)))
        end = date.fromisoformat(request.GET.get("end", str(preset_end)))
        if start > end:
            raise ValueError()
    except ValueError:
        start = end = today
        date_error = "Choose a valid date range. Showing today."
    tab = request.GET.get("tab", "overview")
    if tab not in dict(REPORT_TABS):
        tab = "overview"
    query = request.GET.get("q", "").strip()[:160]
    payment = request.GET.get("payment", "") if tab == "sales" else ""
    if payment not in dict(Sale.Payment.choices):
        payment = ""
    supplier = request.GET.get("supplier", "")[:160] if tab == "purchases" else ""
    data = build_report(start, end)
    rows = select_rows(data, tab, query, payment, supplier)
    if request.GET.get("format") == "csv":
        return report_csv(data, tab, rows, start, end)
    print_all = request.GET.get("print") == "1"
    page = Paginator(rows, max(len(rows), 1) if print_all else 25).get_page(
        request.GET.get("page")
    )
    filters = {"start": start.isoformat(), "end": end.isoformat(), "tab": tab}
    if query:
        filters["q"] = query
    if payment:
        filters["payment"] = payment
    if supplier:
        filters["supplier"] = supplier
    active_period = (
        period
        if ranges.get(period) == (start, end)
        else next(
            (code for code, bounds in ranges.items() if bounds == (start, end)),
            "custom",
        )
    )
    period_links = []
    for code, label in [
        ("today", "Today"),
        ("yesterday", "Yesterday"),
        ("week", "Last 7 days"),
        ("month", "This month"),
    ]:
        params = {
            key: value for key, value in filters.items() if key not in ("start", "end")
        }
        params["period"] = code
        period_links.append(
            {
                "label": label,
                "url": "?" + urlencode(params),
                "active": active_period == code and not date_error,
            }
        )
    tabs = [
        {
            "key": key,
            "label": label,
            "url": "?"
            + urlencode(
                {"start": start.isoformat(), "end": end.isoformat(), "tab": key}
            ),
        }
        for key, label in REPORT_TABS
    ]
    data.update(
        {
            "start": start,
            "end": end,
            "date_error": date_error,
            "period": active_period,
            "tab": tab,
            "tab_label": dict(REPORT_TABS)[tab],
            "report_tabs": tabs,
            "period_links": period_links,
            "query": query,
            "payment": payment,
            "payment_label": dict(Sale.Payment.choices).get(payment, ""),
            "supplier": supplier,
            "payment_choices": Sale.Payment.choices,
            "supplier_choices": sorted(
                {row["supplier"] for row in data["purchase_rows"]}, key=str.casefold
            ),
            "page": page,
            "row_count": len(rows),
            "report_metrics": metrics(data, tab, rows),
            "export_url": "?" + urlencode({**filters, "format": "csv"}),
            "print_url": "?" + urlencode({**filters, "print": "1"}),
            "print_all": print_all,
            "previous_page_url": "?"
            + urlencode({**filters, "page": page.previous_page_number()})
            if page.has_previous()
            else "",
            "next_page_url": "?"
            + urlencode({**filters, "page": page.next_page_number()})
            if page.has_next()
            else "",
            "recent_sale_rows": data["sale_rows"][:5],
            "recent_purchase_rows": data["purchase_rows"][:5],
            "recent_days": data["daily_rows"][:7],
        }
    )
    return render(request, "shop/reports.html", data)


@staff_member_required
@permission_required("shop.add_sale", raise_exception=True)
def billing_view(request):
    if not request.user.has_perm("shop.post_sale"):
        raise PermissionDenied
    import uuid
    from django.db import transaction, IntegrityError
    from django.core.exceptions import ValidationError
    from .forms import BillingForm, BillingLineFormSet
    from .models import SaleLine, ShopSettings
    from .services import post_sale

    profile = ShopSettings.objects.filter(pk=1).first() or ShopSettings()
    tokens = request.session.get("checkout_tokens", [])
    form = BillingForm(request.POST or None, tax_mode=profile.mode)
    formset = BillingLineFormSet(request.POST or None, prefix="items")
    if request.method == "POST":
        form_valid = form.is_valid()
        lines_valid = formset.is_valid()
        if form_valid and str(form.cleaned_data["token"]) not in tokens:
            form.add_error(
                None, "This checkout has expired. Reload the billing screen."
            )
            form_valid = False
        if form_valid and lines_valid:
            existing = Sale.objects.filter(
                checkout_token=form.cleaned_data["token"], posted=True
            ).first()
            if existing:
                return sale_success_response(request, existing)
            try:
                with transaction.atomic():
                    rows = [
                        line.cleaned_data
                        for line in formset
                        if line.cleaned_data and not line.cleaned_data.get("DELETE")
                    ]
                    ids = {row["product"].pk for row in rows}
                    products = {
                        p.pk: p
                        for p in Product.objects.select_for_update()
                        .filter(pk__in=ids)
                        .order_by("pk")
                    }
                    sale = form.save(commit=False)
                    sale.checkout_token = form.cleaned_data["token"]
                    sale.save()
                    for row in rows:
                        product = products[row["product"].pk]
                        SaleLine.objects.create(
                            sale=sale,
                            product=product,
                            quantity=row["quantity"],
                            unit_price=product.sale_price,
                        )
                    post_sale(sale.pk, actor=request.user)
                return sale_success_response(request, sale)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            except IntegrityError:
                existing = Sale.objects.filter(
                    checkout_token=form.cleaned_data["token"], posted=True
                ).first()
                if existing:
                    return sale_success_response(request, existing)
                raise
    if request.method != "POST":
        token = str(uuid.uuid4())
        request.session["checkout_tokens"] = (tokens + [token])[-20:]
        form = BillingForm(
            initial={"token": token, "discount": 0}, tax_mode=profile.mode
        )
    products = [
        {
            "id": p.pk,
            "name": p.name,
            "price": str(p.sale_price),
            "stock": str(p.stock),
            "unit": p.unit,
            "tax_rate": str(p.tax_rate),
        }
        for p in Product.objects.filter(active=True).order_by("name")
    ]
    return render(
        request,
        "shop/billing.html",
        {
            "form": form,
            "formset": formset,
            "products": products,
            "profile": profile,
            "has_sale_stock": any(
                Decimal(product["stock"]) > 0 for product in products
            ),
            "last_sale": Sale.objects.filter(
                pk=request.session.get("last_sale_id"), posted=True
            ).first()
            if request.user.has_perm("shop.view_sale")
            else None,
        },
    )


@staff_member_required
@permission_required("shop.view_returndocument", raise_exception=True)
def return_receipt_view(request, pk):
    from .models import ReturnDocument

    document = get_object_or_404(
        ReturnDocument.objects.select_related("sale", "purchase").prefetch_related(
            "lines__sale_line", "lines__purchase_line"
        ),
        pk=pk,
        posted=True,
    )
    profile = document.sale.invoice_snapshot if document.sale_id else {}
    return render(
        request, "shop/return_receipt.html", {"document": document, "profile": profile}
    )


def health_view(request):
    from django.db import connection
    from django.http import JsonResponse

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@staff_member_required
@permission_required(
    ("shop.add_returndocument", "shop.post_return"), raise_exception=True
)
def create_return_view(request, kind, pk):
    import uuid
    from django.db import transaction, IntegrityError
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from .forms import ReturnForm, ReturnQuantityFormSet
    from .models import ReturnDocument, ReturnLine
    from .services import post_return

    if kind not in ("sale", "purchase"):
        raise PermissionDenied
    if not request.user.has_perm(f"shop.view_{kind}"):
        raise PermissionDenied
    source = get_object_or_404(Sale if kind == "sale" else Purchase, pk=pk, posted=True)
    originals = list(source.lines.select_related("product").order_by("pk"))
    ids = {line.pk: line for line in originals}
    initial = []
    for line in originals:
        field = "sale_line" if kind == "sale" else "purchase_line"
        returned = ReturnLine.objects.filter(
            **{field: line}, document__posted=True
        ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        initial.append(
            {
                "source_line": line.pk,
                "quantity": 0,
                "remaining": line.quantity - returned,
            }
        )
    tokens = request.session.get("return_tokens", [])
    initial_form = {"payment_method": getattr(source, "payment_method", "cash")}
    if request.method != "POST":
        token = str(uuid.uuid4())
        request.session["return_tokens"] = (tokens + [token])[-20:]
        initial_form["token"] = token
    form = ReturnForm(request.POST or None, initial=initial_form)
    formset = ReturnQuantityFormSet(
        request.POST or None, initial=initial, prefix="returns"
    )
    if request.method == "POST":
        valid = form.is_valid()
        lines_valid = formset.is_valid()
        if valid and str(form.cleaned_data["token"]) not in tokens:
            form.add_error(None, "Return form expired. Reload this page.")
            valid = False
        if valid and lines_valid:
            existing = ReturnDocument.objects.filter(
                submission_token=form.cleaned_data["token"], posted=True
            ).first()
            if existing:
                return redirect("return_receipt", pk=existing.pk)
            try:
                with transaction.atomic():
                    document = ReturnDocument.objects.create(
                        **{kind: source},
                        submission_token=form.cleaned_data["token"],
                        reason=form.cleaned_data["reason"],
                        payment_method=form.cleaned_data["payment_method"],
                    )
                    seen = set()
                    for row in formset.cleaned_data:
                        if not row:
                            continue
                        original = ids.get(row["source_line"])
                        if original is None or original.pk in seen:
                            raise ValidationError("Invalid or repeated original line")
                        seen.add(original.pk)
                        if not row["quantity"]:
                            continue
                        ReturnLine.objects.create(
                            document=document,
                            **{
                                (
                                    "sale_line" if kind == "sale" else "purchase_line"
                                ): original
                            },
                            quantity=row["quantity"],
                            restock=row["restock"] if kind == "sale" else False,
                        )
                    post_return(document.pk, actor=request.user)
                return redirect("return_receipt", pk=document.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            except IntegrityError:
                existing = ReturnDocument.objects.filter(
                    submission_token=form.cleaned_data["token"], posted=True
                ).first()
                if existing:
                    return redirect("return_receipt", pk=existing.pk)
                raise
    rows = []
    remaining = {item["source_line"]: item["remaining"] for item in initial}
    for row in formset:
        try:
            original = ids.get(int(row["source_line"].value()))
        except (TypeError, ValueError):
            original = None
        rows.append(
            {
                "form": row,
                "original": original,
                "remaining": remaining.get(original.pk) if original else 0,
            }
        )
    return render(
        request,
        "shop/create_return.html",
        {
            "form": form,
            "formset": formset,
            "rows": rows,
            "source": source,
            "kind": kind,
        },
    )


@staff_member_required
@permission_required(("shop.add_purchase", "shop.post_purchase"), raise_exception=True)
def purchase_entry_view(request):
    import uuid
    from django.contrib import messages
    from django.db import transaction, IntegrityError
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from .forms import PurchaseEntryForm, PurchaseEntryLineFormSet
    from .models import PurchaseLine, ShopSettings
    from .services import post_purchase

    tokens = request.session.get("purchase_tokens", [])
    form = PurchaseEntryForm(request.POST or None)
    formset = PurchaseEntryLineFormSet(request.POST or None, prefix="items")
    if request.method == "POST":
        valid = form.is_valid()
        lines_valid = formset.is_valid()
        if valid and str(form.cleaned_data["token"]) not in tokens:
            form.add_error(None, "This purchase form has expired. Reload the page.")
            valid = False
        if valid and lines_valid:
            existing = Purchase.objects.filter(
                entry_token=form.cleaned_data["token"], posted=True
            ).first()
            if existing:
                return redirect("purchase_receipt", pk=existing.pk)
            try:
                with transaction.atomic():
                    purchase = form.save(commit=False)
                    purchase.entry_token = form.cleaned_data["token"]
                    purchase.save()
                    for row in formset.cleaned_data:
                        if not row or row.get("DELETE"):
                            continue
                        PurchaseLine.objects.create(
                            purchase=purchase,
                            product=row["product"],
                            quantity=row["quantity"],
                            unit_cost=row["unit_cost"],
                        )
                    post_purchase(purchase.pk, actor=request.user)
                messages.success(request, "Purchase saved. Stock updated.")
                return redirect("purchase_receipt", pk=purchase.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            except IntegrityError:
                existing = Purchase.objects.filter(
                    entry_token=form.cleaned_data["token"], posted=True
                ).first()
                if existing:
                    return redirect("purchase_receipt", pk=existing.pk)
                raise
    if request.method != "POST":
        token = str(uuid.uuid4())
        request.session["purchase_tokens"] = (tokens + [token])[-20:]
        form = PurchaseEntryForm(initial={"token": token})
    products = [
        {
            "id": p.pk,
            "name": p.name,
            "price": format(p.suggested_purchase_price, ".2f"),
            "stock": str(p.stock),
            "unit": p.unit,
            "tax_rate": "0",
        }
        for p in Product.objects.filter(active=True)
        .with_purchase_price()
        .order_by("name")
    ]
    suppliers = (
        Purchase.objects.exclude(supplier="")
        .order_by("supplier")
        .values_list("supplier", flat=True)
        .distinct()[:100]
    )
    profile = ShopSettings.objects.filter(pk=1).first() or ShopSettings()
    return render(
        request,
        "shop/billing.html",
        {
            "form": form,
            "formset": formset,
            "products": products,
            "profile": profile,
            "purchase_mode": True,
            "suppliers": suppliers,
        },
    )


@staff_member_required
@permission_required("shop.view_purchase", raise_exception=True)
def purchase_receipt_view(request, pk):
    purchase = get_object_or_404(
        Purchase.objects.prefetch_related("lines__product"), pk=pk, posted=True
    )
    return render(request, "shop/purchase_receipt.html", {"purchase": purchase})


def sale_success_response(request, sale):
    from django.shortcuts import redirect
    from django.urls import reverse
    from django.contrib import messages

    request.session["last_sale_id"] = sale.pk
    action = request.POST.get("action", "invoice")
    if action == "next":
        messages.success(
            request,
            f"Bill {sale.invoice_number or sale.pk} saved. Total ₹{sale.total}.",
        )
        return redirect("billing")
    if action == "print":
        return redirect(reverse("invoice", args=[sale.pk]) + "?paper=thermal&print=1")
    return redirect("invoice", pk=sale.pk)


@staff_member_required
def home_view(request):
    from django.shortcuts import redirect

    if request.user.has_perm("shop.add_sale") and request.user.has_perm(
        "shop.post_sale"
    ):
        return redirect("billing")
    if request.user.has_perm("shop.add_purchase") and request.user.has_perm(
        "shop.post_purchase"
    ):
        return redirect("purchase_entry")
    if request.user.has_perm("shop.view_reports"):
        return redirect("reports")
    return redirect("admin:index")


@staff_member_required
@permission_required("shop.view_product", raise_exception=True)
def items_view(request):
    from django.core.paginator import Paginator

    items = Product.objects.order_by("name", "pk")
    query = request.GET.get("q", "").strip()[:160]
    removed = request.GET.get("show") == "removed"
    items = items.filter(active=not removed)
    if query:
        items = items.filter(name__icontains=query)
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    return render(
        request, "shop/items.html", {"page": page, "query": query, "removed": removed}
    )


def item_audit_values(product):
    return {
        field: str(getattr(product, field))
        for field in (
            "name",
            "unit",
            "default_purchase_price",
            "sale_price",
            "active",
            "category",
            "low_stock_at",
            "hsn",
            "tax_rate",
        )
    }


@staff_member_required
def item_form_view(request, pk=None):
    from django.db import transaction, IntegrityError
    from django.contrib import messages
    from django.shortcuts import redirect
    from .forms import ItemForm
    from .services import audit

    if request.method not in ("GET", "POST"):
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["GET", "POST"])
    permission = "shop.change_product" if pk else "shop.add_product"
    if not request.user.has_perm(permission):
        raise PermissionDenied
    if request.method == "POST":
        try:
            with transaction.atomic():
                product = (
                    get_object_or_404(Product.objects.select_for_update(), pk=pk)
                    if pk
                    else Product()
                )
                before = item_audit_values(product) if pk else None
                form = ItemForm(request.POST, instance=product)
                if form.is_valid():
                    product = form.save()
                    audit(
                        request.user,
                        "item.changed" if pk else "item.created",
                        f"shop.Product #{product.pk}",
                        {"before": before, "after": item_audit_values(product)},
                    )
                    messages.success(request, f"{product.name} saved.")
                    if request.user.has_perm("shop.view_product"):
                        return redirect("items")
                    return redirect("home")
        except IntegrityError:
            form.add_error(
                "name", "An item with this name already exists. Use a different name."
            )
    else:
        product = get_object_or_404(Product, pk=pk) if pk else Product()
        form = ItemForm(instance=product)
    return render(
        request,
        "shop/item_form.html",
        {"form": form, "product": product, "editing": bool(pk)},
    )


@staff_member_required
@permission_required("shop.change_product", raise_exception=True)
def item_remove_view(request, pk):
    from django.db import transaction
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.http import HttpResponseNotAllowed
    from .services import audit

    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        with transaction.atomic():
            product = get_object_or_404(Product.objects.select_for_update(), pk=pk)
            if product.active:
                product.active = False
                product.save(update_fields=["active"])
                audit(
                    request.user,
                    "item.removed",
                    f"shop.Product #{pk}",
                    {"name": product.name, "stock": str(product.stock)},
                )
            messages.success(request, f"{product.name} removed from active items.")
        return redirect("items")
    return render(
        request, "shop/item_remove.html", {"product": get_object_or_404(Product, pk=pk)}
    )


@staff_member_required
@permission_required("shop.change_product", raise_exception=True)
def item_restore_view(request, pk):
    from django.db import transaction
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.http import HttpResponseNotAllowed
    from .services import audit

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    with transaction.atomic():
        product = get_object_or_404(Product.objects.select_for_update(), pk=pk)
        if not product.active:
            product.active = True
            product.save(update_fields=["active"])
            audit(
                request.user,
                "item.restored",
                f"shop.Product #{pk}",
                {"name": product.name},
            )
        messages.success(request, f"{product.name} restored.")
    return redirect("items")
