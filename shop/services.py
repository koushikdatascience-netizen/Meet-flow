from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Product, Purchase, Sale, StockMovement, money


@transaction.atomic
def post_purchase(pk, actor=None):
    purchase = Purchase.objects.select_for_update().get(pk=pk)
    if purchase.posted:
        raise ValidationError("Purchase already posted")
    lines = list(purchase.lines.select_related("product"))
    if not lines:
        raise ValidationError("Add at least one purchase line")
    for line in lines:
        line.full_clean()
    products = {
        p.pk: p
        for p in Product.objects.select_for_update()
        .filter(pk__in={line.product_id for line in lines})
        .order_by("pk")
    }
    for line in lines:
        validate_quantity(products[line.product_id], line.quantity)
    for pid, product in products.items():
        product_lines = [line for line in lines if line.product_id == pid]
        quantity = sum((line.quantity for line in product_lines), Decimal("0"))
        cost = sum(
            (line.quantity * line.unit_cost for line in product_lines), Decimal("0")
        )
        new_stock = product.stock + quantity
        product.average_cost = money(
            (product.stock * product.average_cost + cost) / new_stock
        )
        product.stock = new_stock
        product.full_clean()
        product.save(update_fields=["stock", "average_cost"])
    for line in lines:
        StockMovement.objects.create(
            product=products[line.product_id],
            quantity_delta=line.quantity,
            reason="purchase",
            reference=f"Purchase #{pk}",
        )
    purchase.posted = True
    purchase.posted_at = timezone.now()
    purchase.save(update_fields=["posted", "posted_at"])
    audit(actor, "purchase.posted", f"Purchase #{pk}", {"total": str(purchase.total)})


@transaction.atomic
def post_sale(pk, actor=None):
    sale = Sale.objects.select_for_update().get(pk=pk)
    if sale.posted:
        raise ValidationError("Sale already posted")
    lines = list(sale.lines.select_related("product"))
    if not lines:
        raise ValidationError("Add at least one sale line")
    for line in lines:
        line.full_clean()
    sale.full_clean()
    if sale.discount > sum((x.amount for x in lines), Decimal("0")):
        raise ValidationError("Discount exceeds subtotal")
    required = {}
    for line in lines:
        required[line.product_id] = (
            required.get(line.product_id, Decimal("0")) + line.quantity
        )
    products = {
        p.pk: p
        for p in Product.objects.select_for_update()
        .filter(pk__in=required)
        .order_by("pk")
    }
    for line in lines:
        validate_quantity(products[line.product_id], line.quantity)
    for pid, qty in required.items():
        if not products[pid].active:
            raise ValidationError(f"{products[pid]} is inactive")
        if products[pid].stock < qty:
            raise ValidationError(
                f"Insufficient stock for {products[pid]}: {products[pid].stock} available"
            )
    for line in lines:
        product = products[line.product_id]
        product.stock -= line.quantity
        product.full_clean()
        product.save(update_fields=["stock"])
        line.cost_at_sale = product.average_cost
        line.product_name = product.name
        line.product_unit = product.unit
        line.save(update_fields=["cost_at_sale", "product_name", "product_unit"])
        StockMovement.objects.create(
            product=product,
            quantity_delta=-line.quantity,
            reason="sale",
            reference=f"Bill #{pk}",
        )
    prepare_invoice(sale, lines, products)
    sale.full_clean()
    sale.posted = True
    sale.posted_at = timezone.now()
    sale.save()
    audit(
        actor,
        "sale.posted",
        sale.invoice_number,
        {"total": str(sale.total), "tax": str(sale.tax_total)},
    )


@transaction.atomic
def post_adjustment(pk, actor=None):
    from .models import StockAdjustment

    adjustment = StockAdjustment.objects.select_for_update().get(pk=pk)
    if adjustment.posted:
        raise ValidationError("Adjustment already posted")
    adjustment.full_clean()
    product = Product.objects.select_for_update().get(pk=adjustment.product_id)
    validate_quantity(product, adjustment.quantity_delta)
    new_stock = product.stock + adjustment.quantity_delta
    if new_stock < 0:
        raise ValidationError("Adjustment would make stock negative")
    product.stock = new_stock
    product.full_clean()
    product.save(update_fields=["stock"])
    adjustment.cost_at_posting = product.average_cost
    adjustment.posted = True
    adjustment.posted_at = timezone.now()
    adjustment.save(update_fields=["cost_at_posting", "posted", "posted_at"])
    StockMovement.objects.create(
        product=product,
        quantity_delta=adjustment.quantity_delta,
        reason=adjustment.reason,
        reference=f"Adjustment #{pk}",
    )
    audit(
        actor,
        "adjustment.posted",
        f"Adjustment #{pk}",
        {"quantity": str(adjustment.quantity_delta), "notes": adjustment.notes},
    )


def audit(actor, action, reference, details=None):
    from .models import AuditEvent

    AuditEvent.objects.create(
        actor=actor.get_username() if actor else "system",
        action=action,
        reference=reference,
        details=details or {},
    )


def next_number(prefix="MF"):
    from django.utils import timezone
    from .models import InvoiceSequence

    today = timezone.localdate()
    year = today.year if today.month >= 4 else today.year - 1
    financial_year = f"{year % 100:02d}-{(year + 1) % 100:02d}"
    key = f"{prefix}/{financial_year}"
    # A unique sequence row plus a row lock serializes numbering on PostgreSQL.
    InvoiceSequence.objects.get_or_create(financial_year=key)
    sequence = InvoiceSequence.objects.select_for_update().get(financial_year=key)
    if sequence.next_number > 999999:
        raise ValidationError("Invoice sequence exhausted")
    number = f"{prefix}/{financial_year}/{sequence.next_number:06d}"
    sequence.next_number += 1
    sequence.save(update_fields=["next_number"])
    return number


def prepare_invoice(sale, lines, products):
    import re
    from .models import ShopSettings

    profile = ShopSettings.objects.filter(pk=1).first() or ShopSettings()
    profile.full_clean()
    taxable = profile.mode == ShopSettings.Mode.TAX
    if profile.mode != ShopSettings.Mode.RECEIPT:
        from .states import normalize_state

        sale.customer_gstin = sale.customer_gstin.strip().upper()
        try:
            sale.customer_state = normalize_state(sale.customer_state)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if sale.customer_gstin:
            if not re.fullmatch(
                r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]", sale.customer_gstin
            ):
                raise ValidationError("Customer GSTIN is invalid")
            if not sale.customer_state:
                try:
                    sale.customer_state = normalize_state(sale.customer_gstin[:2])
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
            if (
                not sale.customer_name
                or not sale.customer_address
                or sale.customer_state != sale.customer_gstin[:2]
            ):
                raise ValidationError(
                    "Registered customers require name, address and matching state"
                )
    if (
        profile.mode != ShopSettings.Mode.RECEIPT
        and sale.subtotal - sale.discount >= 50000
        and (
            not sale.customer_name
            or not sale.customer_address
            or not sale.customer_state
        )
    ):
        raise ValidationError(
            "Customer name, address and state are required for this invoice value"
        )
    interstate = profile.mode != ShopSettings.Mode.RECEIPT and bool(
        sale.customer_state and sale.customer_state != profile.state_code
    )
    subtotal = sum((line.amount for line in lines), Decimal("0"))
    allocated = Decimal("0")
    for index, line in enumerate(lines):
        product = products[line.product_id]
        # Cumulative allocation avoids a negative final line after many tiny lines.
        cumulative = sum((x.amount for x in lines[: index + 1]), Decimal("0"))
        target = (
            money(sale.discount * cumulative / subtotal) if subtotal else Decimal("0")
        )
        discount = target - allocated
        allocated = target
        line.discount_amount = discount
        line.taxable_amount = line.amount - discount
        line.hsn = product.hsn
        line.tax_rate = product.tax_rate if taxable else Decimal("0")
        line.cgst = line.sgst = line.igst = Decimal("0")
        if profile.mode != ShopSettings.Mode.RECEIPT and not re.fullmatch(
            r"(?:[0-9]{4}|[0-9]{6}|[0-9]{8})", product.hsn
        ):
            raise ValidationError(f"Configure HSN for {product.name}")
        if taxable:
            if interstate:
                line.igst = money(line.taxable_amount * line.tax_rate / 100)
            else:
                line.cgst = money(line.taxable_amount * line.tax_rate / 200)
                line.sgst = line.cgst
        line.full_clean()
        line.save(
            update_fields=[
                "discount_amount",
                "taxable_amount",
                "hsn",
                "tax_rate",
                "cgst",
                "sgst",
                "igst",
            ]
        )
    sale.taxable_total = sum((line.taxable_amount for line in lines), Decimal("0"))
    sale.tax_total = sum(
        (line.cgst + line.sgst + line.igst for line in lines), Decimal("0")
    )
    sale.charged_total = sale.taxable_total + sale.tax_total
    if (
        profile.mode != ShopSettings.Mode.RECEIPT
        and sale.charged_total >= 50000
        and (
            not sale.customer_name
            or not sale.customer_address
            or not sale.customer_state
        )
    ):
        raise ValidationError(
            "Customer name, address and state are required for this invoice value"
        )
    sale.invoice_number = next_number()
    sale.invoice_snapshot = {
        field: getattr(profile, field)
        for field in (
            "name",
            "address",
            "phone",
            "gstin",
            "state_code",
            "mode",
            "footer",
            "signature",
        )
    }
    sale.invoice_snapshot["place_of_supply"] = sale.customer_state or profile.state_code
    sale.invoice_snapshot["interstate"] = interstate


@transaction.atomic
def post_return(pk, actor=None):
    from django.db.models import Sum
    from .models import ReturnDocument, ReturnLine

    document = ReturnDocument.objects.select_for_update().get(pk=pk)
    if document.posted:
        raise ValidationError("Return already posted")
    document.full_clean()
    source = (
        (Sale if document.sale_id else Purchase)
        .objects.select_for_update()
        .get(pk=document.sale_id or document.purchase_id)
    )
    if not source.posted:
        raise ValidationError("Only posted transactions can be returned")
    lines = list(
        document.lines.select_related("sale_line", "purchase_line").order_by("pk")
    )
    if not lines:
        raise ValidationError("Add at least one return line")
    origin_ids = []
    origins = []
    for line in lines:
        line.full_clean()
        origin = line.sale_line if document.sale_id else line.purchase_line
        if (
            not origin
            or (origin.sale_id if document.sale_id else origin.purchase_id) != source.pk
        ):
            raise ValidationError(
                "Return lines must belong to the selected transaction"
            )
        if origin.pk in origin_ids:
            raise ValidationError("Use a single return line per original line")
        origin_ids.append(origin.pk)
        origins.append(origin)
    products = {
        p.pk: p
        for p in Product.objects.select_for_update()
        .filter(pk__in={x.product_id for x in origins})
        .order_by("pk")
    }
    total = tax_total = profit = variance = Decimal("0")
    for line, origin in zip(lines, origins):
        field = "sale_line" if document.sale_id else "purchase_line"
        returned = ReturnLine.objects.filter(
            **{field: origin}, document__posted=True
        ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        if returned + line.quantity > origin.quantity:
            raise ValidationError(
                "Returned quantity exceeds the remaining original quantity"
            )
        product = products[origin.product_id]
        validate_quantity(product, line.quantity)

        def portion(value):
            return money(value * (returned + line.quantity) / origin.quantity) - money(
                value * returned / origin.quantity
            )

        if document.sale_id:
            modern = bool(source.invoice_snapshot)
            if modern:
                base = origin.taxable_amount
            else:
                previous = sum(
                    (x.amount for x in source.lines.filter(pk__lt=origin.pk)),
                    Decimal("0"),
                )
                allocated = (
                    (
                        money(
                            source.discount
                            * (previous + origin.amount)
                            / source.subtotal
                        )
                        - money(source.discount * previous / source.subtotal)
                    )
                    if source.subtotal
                    else Decimal("0")
                )
                base = origin.amount - allocated
            line.cgst = portion(origin.cgst) if modern else Decimal("0")
            line.sgst = portion(origin.sgst) if modern else Decimal("0")
            line.igst = portion(origin.igst) if modern else Decimal("0")
            line.tax_amount = line.cgst + line.sgst + line.igst
            net = portion(base)
            line.taxable_amount = net
            line.amount = net + line.tax_amount
            line.cost_amount = (
                portion(money(origin.quantity * origin.cost_at_sale))
                if line.restock
                else Decimal("0")
            )
            profit += net - line.cost_amount
            if line.restock:
                new_stock = product.stock + line.quantity
                product.average_cost = money(
                    (
                        product.stock * product.average_cost
                        + line.quantity * origin.cost_at_sale
                    )
                    / new_stock
                )
                product.stock = new_stock
                product.full_clean()
                product.save(update_fields=["stock", "average_cost"])
                StockMovement.objects.create(
                    product=product,
                    quantity_delta=line.quantity,
                    reason="sale_return",
                    reference=f"Return #{pk}",
                )
        else:
            if line.restock:
                raise ValidationError("Restock is only applicable to customer returns")
            if product.stock < line.quantity:
                raise ValidationError(
                    f"Insufficient stock to return {product.name} to supplier"
                )
            line.amount = portion(money(origin.quantity * origin.unit_cost))
            line.cost_amount = money(line.quantity * product.average_cost)
            variance += line.amount - line.cost_amount
            product.stock -= line.quantity
            product.full_clean()
            product.save(update_fields=["stock"])
            StockMovement.objects.create(
                product=product,
                quantity_delta=-line.quantity,
                reason="purchase_return",
                reference=f"Return #{pk}",
            )
        line.full_clean()
        line.save(
            update_fields=[
                "amount",
                "tax_amount",
                "cost_amount",
                "taxable_amount",
                "cgst",
                "sgst",
                "igst",
            ]
        )
        total += line.amount
        tax_total += line.tax_amount
    document.amount = total
    document.tax_amount = tax_total
    document.profit_reversal = profit
    document.valuation_variance = variance
    document.document_number = next_number("CN" if document.sale_id else "PR")
    document.full_clean()
    document.posted = True
    document.posted_at = timezone.now()
    document.save()
    audit(
        actor,
        "return.posted",
        document.document_number,
        {"source": str(source), "amount": str(total), "reason": document.reason},
    )
    return document


def validate_quantity(product, quantity):
    if product.unit == Product.Unit.PIECE and quantity != quantity.to_integral_value():
        raise ValidationError(
            f"{product.name} is sold by piece; enter whole quantities"
        )
