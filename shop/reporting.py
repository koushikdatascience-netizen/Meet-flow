"""Read-only operational reports using recorded transaction values, never current prices."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from .models import (
    Sale,
    Purchase,
    Product,
    Expense,
    StockAdjustment,
    ReturnDocument,
    money,
)

ZERO = Decimal("0.00")
REPORT_TABS = (
    ("overview", "Overview"),
    ("sales", "Sales & bills"),
    ("purchases", "Purchases"),
    ("items", "Item performance"),
    ("returns", "Returns"),
)


def total(values):
    return sum(values, ZERO)


def quantity_text(values):
    quantities = defaultdict(lambda: Decimal("0"))
    for quantity, unit in values:
        quantities[unit] += quantity
    return (
        ", ".join(
            f"{format(quantity.normalize(), 'f')} {unit}"
            for unit, quantity in sorted(quantities.items())
        )
        or "—"
    )


def posted_in(model, start, end):
    return (
        model.objects.annotate(report_date=Coalesce("posted_at", "created_at"))
        .filter(posted=True, report_date__date__range=(start, end))
        .order_by("-report_date", "-pk")
    )


def build_report(start, end):
    sales = list(posted_in(Sale, start, end).prefetch_related("lines__product"))
    purchases = list(posted_in(Purchase, start, end).prefetch_related("lines__product"))
    returns = list(
        posted_in(ReturnDocument, start, end)
        .select_related("sale", "purchase")
        .prefetch_related("lines__sale_line__product", "lines__purchase_line__product")
    )
    sale_rows = []
    purchase_rows = []
    return_rows = []
    items = {}
    suppliers = {}
    daily = {}

    def day(document):
        value = timezone.localtime(document.report_date).date()
        return daily.setdefault(
            value,
            {
                "date": value,
                "bills": 0,
                "billed": ZERO,
                "refunds": ZERO,
                "net_sales": ZERO,
                "purchases": ZERO,
                "supplier_returns": ZERO,
                "net_purchases": ZERO,
            },
        )

    def item(product, unit, name):
        key = (product.pk, unit)
        return items.setdefault(
            key,
            {
                "id": product.pk,
                "name": name,
                "unit": unit,
                "sold": ZERO,
                "customer_returns": ZERO,
                "restocked": ZERO,
                "net_sold": ZERO,
                "purchased": ZERO,
                "supplier_returns": ZERO,
                "net_purchased": ZERO,
                "revenue": ZERO,
                "purchase_value": ZERO,
                "gross_profit": ZERO,
            },
        )

    def supplier(name):
        name = name or "Not entered"
        return suppliers.setdefault(
            name,
            {
                "name": name,
                "receipts": 0,
                "received": ZERO,
                "credits": ZERO,
                "net": ZERO,
            },
        )

    for sale in sales:
        lines = sorted(sale.lines.all(), key=lambda line: line.pk)
        sale_rows.append(
            {
                "document": sale,
                "id": sale.pk,
                "number": sale.invoice_number or f"Bill #{sale.pk}",
                "date": sale.report_date,
                "customer": sale.customer_name or "Walk-in",
                "payment": sale.get_payment_method_display(),
                "payment_code": sale.payment_method,
                "lines": len(lines),
                "quantity": quantity_text(
                    (line.quantity, line.product_unit or line.product.unit)
                    for line in lines
                ),
                "subtotal": sale.subtotal,
                "discount": sale.discount,
                "tax": sale.tax_total,
                "amount": sale.total,
                "gross_profit": sale.gross_profit,
                "url": reverse("invoice", args=[sale.pk]),
            }
        )
        current_day = day(sale)
        current_day["bills"] += 1
        current_day["billed"] += sale.total
        cumulative = ZERO
        allocated = ZERO
        for line in lines:
            cumulative += line.amount
            discount = (
                money(sale.discount * cumulative / sale.subtotal)
                if sale.subtotal
                else ZERO
            )
            base = (
                line.taxable_amount
                if sale.invoice_snapshot
                else line.amount - (discount - allocated)
            )
            allocated = discount
            row = item(
                line.product,
                line.product_unit or line.product.unit,
                line.product_name or line.product.name,
            )
            row["sold"] += line.quantity
            row["revenue"] += base
            row["gross_profit"] += base - money(line.quantity * line.cost_at_sale)
    for purchase in purchases:
        lines = list(purchase.lines.all())
        purchase_rows.append(
            {
                "document": purchase,
                "id": purchase.pk,
                "number": f"Purchase #{purchase.pk}",
                "date": purchase.report_date,
                "supplier": purchase.supplier or "Not entered",
                "lines": len(lines),
                "quantity": quantity_text(
                    (line.quantity, line.product.unit) for line in lines
                ),
                "amount": purchase.total,
                "url": reverse("purchase_receipt", args=[purchase.pk]),
            }
        )
        current_supplier = supplier(purchase.supplier)
        current_supplier["receipts"] += 1
        current_supplier["received"] += purchase.total
        day(purchase)["purchases"] += purchase.total
        for line in lines:
            row = item(line.product, line.product.unit, line.product.name)
            row["purchased"] += line.quantity
            row["purchase_value"] += money(line.quantity * line.unit_cost)
    for document in returns:
        original = document.sale if document.sale_id else document.purchase
        lines = list(document.lines.all())
        return_rows.append(
            {
                "document": document,
                "id": document.pk,
                "number": document.document_number or f"Return #{document.pk}",
                "date": document.report_date,
                "kind": "Customer refund" if document.sale_id else "Supplier return",
                "source": (original.invoice_number or f"Bill #{original.pk}")
                if document.sale_id
                else f"Purchase #{original.pk}",
                "party": (original.customer_name or "Walk-in")
                if document.sale_id
                else (original.supplier or "Not entered"),
                "payment": document.get_payment_method_display(),
                "reason": document.reason,
                "amount": document.amount,
                "tax": document.tax_amount,
                "url": reverse("return_receipt", args=[document.pk]),
            }
        )
        current_day = day(document)
        if document.sale_id:
            current_day["refunds"] += document.amount
        else:
            current_day["supplier_returns"] += document.amount
            supplier(original.supplier)["credits"] += document.amount
        for line in lines:
            origin = line.sale_line if document.sale_id else line.purchase_line
            unit = (
                (origin.product_unit or origin.product.unit)
                if document.sale_id
                else origin.product.unit
            )
            name = (
                (origin.product_name or origin.product.name)
                if document.sale_id
                else origin.product.name
            )
            row = item(origin.product, unit, name)
            if document.sale_id:
                row["customer_returns"] += line.quantity
                if line.restock:
                    row["restocked"] += line.quantity
                row["revenue"] -= line.amount - line.tax_amount
                row["gross_profit"] -= (
                    line.amount - line.tax_amount
                ) - line.cost_amount
            else:
                row["supplier_returns"] += line.quantity
                row["purchase_value"] -= line.amount
    for row in items.values():
        row["net_sold"] = row["sold"] - row["customer_returns"]
        row["net_purchased"] = row["purchased"] - row["supplier_returns"]
    for row in suppliers.values():
        row["net"] = row["received"] - row["credits"]
    for row in daily.values():
        row["net_sales"] = row["billed"] - row["refunds"]
        row["net_purchases"] = row["purchases"] - row["supplier_returns"]
    refunds = total(r.amount for r in returns if r.sale_id)
    supplier_returns = total(r.amount for r in returns if r.purchase_id)
    gross_profit = total(s.gross_profit for s in sales) - total(
        r.profit_reversal for r in returns
    )
    variance = total(r.valuation_variance for r in returns)
    expenses = (
        Expense.objects.filter(date__range=(start, end)).aggregate(value=Sum("amount"))[
            "value"
        ]
        or ZERO
    )
    adjustments = list(posted_in(StockAdjustment, start, end))
    adjustment_value = total(a.value for a in adjustments)
    payment_summary = []
    for code, label in Sale.Payment.choices:
        received = total(s.total for s in sales if s.payment_method == code)
        refunded = total(
            r.amount for r in returns if r.sale_id and r.payment_method == code
        )
        payment_summary.append(
            {
                "method": label,
                "received": received,
                "refunded": refunded,
                "net": received - refunded,
            }
        )
    return {
        "sales_total": total(s.total for s in sales) - refunds,
        "purchase_total": total(p.total for p in purchases) - supplier_returns,
        "gross_profit": gross_profit,
        "expenses": expenses,
        "adjustment_value": adjustment_value,
        "operating_profit": gross_profit + adjustment_value + variance - expenses,
        "refunds": refunds,
        "purchase_returns": supplier_returns,
        "tax_collected": total(s.tax_total for s in sales)
        - total(r.tax_amount for r in returns if r.sale_id),
        "valuation_variance": variance,
        "payment_summary": payment_summary,
        "sales_count": len(sales),
        "purchase_count": len(purchases),
        "recent_sales": sales[:5],
        "recent_purchases": purchases[:5],
        "sale_rows": sale_rows,
        "purchase_rows": purchase_rows,
        "return_rows": return_rows,
        "item_rows": sorted(
            items.values(),
            key=lambda row: (-row["revenue"], row["name"].casefold(), row["unit"]),
        ),
        "supplier_rows": sorted(
            suppliers.values(), key=lambda row: (-row["net"], row["name"].casefold())
        ),
        "daily_rows": sorted(daily.values(), key=lambda row: row["date"], reverse=True),
        "low_stock": Product.objects.filter(
            active=True, stock__lte=F("low_stock_at")
        ).order_by("name"),
        "stock_value": total(p.stock * p.average_cost for p in Product.objects.all()),
    }


def select_rows(data, tab, query="", payment="", supplier=""):
    key = {
        "sales": "sale_rows",
        "purchases": "purchase_rows",
        "items": "item_rows",
        "returns": "return_rows",
    }.get(tab)
    if not key:
        return []
    rows = list(data[key])
    term = query.casefold()
    if term:
        fields = {
            "sales": ("number", "id", "customer"),
            "purchases": ("number", "id", "supplier"),
            "items": ("name",),
            "returns": ("number", "source", "party", "reason"),
        }[tab]
        rows = [
            row
            for row in rows
            if any(term in str(row[field]).casefold() for field in fields)
        ]
    if tab == "sales" and payment:
        rows = [row for row in rows if row["payment_code"] == payment]
    if tab == "purchases" and supplier:
        rows = [row for row in rows if row["supplier"] == supplier]
    return rows


def metrics(data, tab, rows):
    def metric(label, value, currency=True):
        return {"label": label, "value": value, "currency": currency}

    if tab == "sales":
        return [
            metric("Bills issued", len(rows), False),
            metric("Billed value", total(r["amount"] for r in rows)),
            metric("Discounts given", total(r["discount"] for r in rows)),
            metric("GST on bills", total(r["tax"] for r in rows)),
        ]
    if tab == "purchases":
        return [
            metric("Purchase receipts", len(rows), False),
            metric("Purchase value", total(r["amount"] for r in rows)),
            metric("Suppliers", len({r["supplier"] for r in rows}), False),
            metric("Item lines received", sum(r["lines"] for r in rows), False),
        ]
    if tab == "items":
        return [
            metric("Items with activity", len(rows), False),
            metric("Net sales (excl. GST)", total(r["revenue"] for r in rows)),
            metric(
                "Gross profit after refunds", total(r["gross_profit"] for r in rows)
            ),
            metric(
                "Net quantity sold",
                quantity_text((r["net_sold"], r["unit"]) for r in rows),
                False,
            ),
        ]
    if tab == "returns":
        return [
            metric("Credit records", len(rows), False),
            metric(
                "Customer refunds",
                total(r["amount"] for r in rows if r["document"].sale_id),
            ),
            metric(
                "Supplier credits",
                total(r["amount"] for r in rows if r["document"].purchase_id),
            ),
            metric("GST credited", total(r["tax"] for r in rows)),
        ]
    return [
        metric("Net sales after refunds", data["sales_total"]),
        metric("Net purchases after returns", data["purchase_total"]),
        metric("Estimated operating profit", data["operating_profit"]),
        metric("Bills issued", data["sales_count"], False),
    ]


def report_csv(data, tab, rows, start, end):
    import csv

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="meatflow-{tab}-{start}-{end}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)

    def write(values):
        writer.writerow(
            [
                "'" + value
                if isinstance(value, str)
                and value.lstrip().startswith(("=", "+", "-", "@"))
                else value
                for value in values
            ]
        )

    if tab == "overview":
        write(
            [
                "From",
                "To",
                "Sales",
                "Purchases",
                "Gross profit",
                "Expenses",
                "Stock adjustment value",
                "Estimated operating profit",
                "Bills",
                "Customer refunds",
                "Supplier returns",
                "Net GST",
                "Purchase return valuation variance",
            ]
        )
        write(
            [
                start,
                end,
                data["sales_total"],
                data["purchase_total"],
                data["gross_profit"],
                data["expenses"],
                data["adjustment_value"],
                data["operating_profit"],
                data["sales_count"],
                data["refunds"],
                data["purchase_returns"],
                data["tax_collected"],
                data["valuation_variance"],
            ]
        )
        write([])
        write(["Payment method", "Received", "Refunded", "Net receipts"])
        for row in data["payment_summary"]:
            write([row["method"], row["received"], row["refunded"], row["net"]])
        write([])
        write(
            [
                "Date",
                "Bills",
                "Billed",
                "Customer refunds",
                "Net sales",
                "Purchases",
                "Supplier credits",
                "Net purchases",
            ]
        )
        for row in data["daily_rows"]:
            write(
                [
                    row[key]
                    for key in (
                        "date",
                        "bills",
                        "billed",
                        "refunds",
                        "net_sales",
                        "purchases",
                        "supplier_returns",
                        "net_purchases",
                    )
                ]
            )
        write([])
        write(["Supplier", "Receipts", "Purchases", "Credits", "Net purchase cost"])
        for row in data["supplier_rows"]:
            write(
                [row[key] for key in ("name", "receipts", "received", "credits", "net")]
            )
    else:
        columns = {
            "sales": (
                ("number", "Bill"),
                ("date", "Posted at"),
                ("customer", "Customer"),
                ("payment", "Payment"),
                ("lines", "Item lines"),
                ("quantity", "Quantities"),
                ("subtotal", "Subtotal"),
                ("discount", "Discount"),
                ("tax", "GST"),
                ("amount", "Bill total"),
                ("gross_profit", "Gross profit before refunds"),
            ),
            "purchases": (
                ("number", "Purchase"),
                ("date", "Posted at"),
                ("supplier", "Supplier"),
                ("lines", "Item lines"),
                ("quantity", "Quantities"),
                ("amount", "Purchase cost"),
            ),
            "items": (
                ("name", "Item"),
                ("unit", "Unit"),
                ("sold", "Sold quantity"),
                ("customer_returns", "Customer return quantity"),
                ("restocked", "Customer returns restored to stock"),
                ("net_sold", "Net sold"),
                ("purchased", "Received quantity"),
                ("supplier_returns", "Supplier return quantity"),
                ("net_purchased", "Net received"),
                ("revenue", "Net sales excluding GST"),
                ("purchase_value", "Net purchase cost"),
                ("gross_profit", "Gross profit after customer refunds"),
            ),
            "returns": (
                ("number", "Credit"),
                ("date", "Posted at"),
                ("kind", "Type"),
                ("source", "Original transaction"),
                ("party", "Customer or supplier"),
                ("payment", "Method"),
                ("reason", "Reason"),
                ("tax", "GST credit"),
                ("amount", "Credit amount"),
            ),
        }[tab]
        write([label for _, label in columns])
        for row in rows:
            write(
                [
                    timezone.localtime(row[key]).isoformat()
                    if key == "date"
                    else row[key]
                    for key, _ in columns
                ]
            )
    return response
