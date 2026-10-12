"""
خدمة التسيير المالي (Finance Service)
=====================================
خطط الاشتراك، الفواتير، الدفعات، المصاريف، والتقارير المالية.

المبدأ الحاكم: **الفاتورة هي المرجع**. كل دفعة تُسجَّل مقابل فاتورة، وحالة
الفاتورة تُشتق دائماً من مجموع دفعاتها -- لا تُضبط يدوياً أبداً. هذا يمنع
التضارب الذي ينشأ في الأنظمة التي تُحدَّث فيها الحالة يدوياً.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import safe_get, utcnow
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import generate_invoice_number, generate_receipt_number
from app.models import (
    Athlete,
    Expense,
    ExpenseCategory,
    FeePlan,
    Group,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    PeriodType,
    Status,
)

logger = logging.getLogger(__name__)


# =====================================================================
#  أدوات
# =====================================================================
def money(value: float | Decimal | int) -> Decimal:
    """تقريب مبلغ لأقرب دينار/سنت (رقمان عشريان)."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def add_months(base: dt.date, months: int) -> dt.date:
    """إضافة أشهر لتاريخ مع معالجة نهاية الشهر (31 يناير + 1 شهر)."""
    if months == 0:
        return base
    month = base.month - 1 + months
    year = base.year + month // 12
    month = month % 12 + 1
    day = min(base.day, _days_in_month(year, month))
    return dt.date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


PERIOD_MONTHS: dict[str, int] = {
    PeriodType.MONTHLY.value: 1,
    PeriodType.QUARTERLY.value: 3,
    PeriodType.SEMI_ANNUAL.value: 6,
    PeriodType.ANNUAL.value: 12,
    PeriodType.ONE_TIME.value: 0,
}


# =====================================================================
#  خطط الاشتراك
# =====================================================================
async def create_fee_plan(db: AsyncSession, data: dict[str, Any]) -> FeePlan:
    """إنشاء خطة اشتراك جديدة."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("اسم الخطة مطلوب")

    amount = money(data.get("amount") or 0)
    if amount < 0:
        raise ValidationError("المبلغ لا يمكن أن يكون سالباً")

    period = data.get("period", PeriodType.MONTHLY.value)
    if period not in PERIOD_MONTHS:
        raise ValidationError("دورية الاشتراك غير صالحة")

    duration_days = int(data.get("duration_days") or _default_duration(period))
    installments = int(data.get("installments") or 1)
    if installments < 1:
        raise ValidationError("عدد الأقساط يجب أن يكون 1 على الأقل")

    plan = FeePlan(
        name=name,
        description=data.get("description"),
        amount=amount,
        period=period,
        duration_days=duration_days,
        installments=installments,
        sport_id=data.get("sport_id"),
        group_id=data.get("group_id"),
        age_group=data.get("age_group"),
        gender=data.get("gender"),
        discount_percent=money(data.get("discount_percent") or 0),
        sibling_discount=money(data.get("sibling_discount") or 0),
        registration_fee=data.get("registration_fee"),
        status=Status.ACTIVE,
        is_active=True,
        sort_order=data.get("sort_order", 0),
        club_id=data.get("club_id"),
    )
    db.add(plan)
    await db.flush()
    return plan


def _default_duration(period: str) -> int:
    """مدة الاشتراك الافتراضية بالأيام لكل دورية."""
    months = PERIOD_MONTHS.get(period, 1)
    return months * 30 if months else 365


async def update_fee_plan(db: AsyncSession, plan_id: int, data: dict[str, Any]) -> FeePlan:
    """تحديث خطة اشتراك."""
    plan = await get_fee_plan(db, plan_id)

    if data.get("amount") is not None:
        new_amount = money(data["amount"])
        if new_amount < 0:
            raise ValidationError("المبلغ لا يمكن أن يكون سالباً")
        plan.amount = new_amount

    for field in (
        "name", "description", "duration_days", "installments",
        "sport_id", "age_group", "gender", "sort_order",
        "registration_fee", "discount_percent", "sibling_discount",
    ):
        if field in data and data[field] is not None:
            setattr(plan, field, data[field])

    if "period" in data and data["period"]:
        plan.period = data["period"]

    if data.get("is_active") is not None:
        plan.is_active = bool(data["is_active"])
        plan.status = Status.ACTIVE if plan.is_active else Status.INACTIVE

    await db.flush()
    await db.refresh(plan)
    return plan


async def get_fee_plan(db: AsyncSession, plan_id: int) -> FeePlan:
    """جلب خطة اشتراك."""
    plan = await safe_get(db, FeePlan, plan_id)
    if plan is None:
        raise NotFoundError("خطة الاشتراك غير موجودة")
    return plan


async def list_fee_plans(
    db: AsyncSession, *, active_only: bool = True, sport_id: int | None = None
) -> list[FeePlan]:
    """قائمة خطط الاشتراك."""
    stmt = select(FeePlan)
    if active_only:
        stmt = stmt.where(FeePlan.is_active.is_(True))
    if sport_id:
        stmt = stmt.where(FeePlan.sport_id == sport_id)
    stmt = stmt.order_by(FeePlan.sort_order, FeePlan.name)
    return list((await db.execute(stmt)).scalars().all())


# =====================================================================
#  الفواتير
# =====================================================================
async def _unique_invoice_number(db: AsyncSession) -> str:
    """توليد رقم فاتورة فريد."""
    for _ in range(20):
        number = generate_invoice_number()
        exists = await db.execute(
            select(Invoice.id).where(Invoice.invoice_number == number).limit(1)
        )
        if exists.scalar_one_or_none() is None:
            return number
    raise ConflictError("تعذّر توليد رقم فاتورة فريد")


async def create_invoice(
    db: AsyncSession,
    *,
    athlete_id: int,
    fee_plan_id: int | None = None,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    due_date: dt.date | None = None,
    amount: float | None = None,
    discount: float = 0,
    notes: str | None = None,
) -> Invoice:
    """
    إنشاء فاتورة اشتراك.

    عند تمرير ``fee_plan_id`` تُحسب المبالغ والفترة تلقائياً من الخطة،
    مع مراعاة خصم الإخوة.
    """
    athlete = await safe_get(db, Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    subtotal = Decimal("0")
    installments = 1
    installment_no = None
    is_installment = False

    if fee_plan_id:
        plan = await get_fee_plan(db, fee_plan_id)

        # خصم دائم على الخطة
        subtotal = money(plan.amount)
        if plan.discount_percent:
            subtotal = subtotal * (Decimal("100") - money(plan.discount_percent)) / Decimal("100")

        # خصم الإخوة
        sibling_discount = await _sibling_discount(db, athlete, plan)
        if sibling_discount:
            discount = money(sibling_discount)

        months = PERIOD_MONTHS.get(plan.period, 1)
        period_start = period_start or dt.date.today()
        period_end = period_end or add_months(period_start, months) - dt.timedelta(days=1)

        installments = plan.installments
        is_installment = installments > 1
        installment_no = 1
    else:
        if amount is None:
            raise ValidationError("يجب تحديد خطة الاشتراك أو المبلغ")
        subtotal = money(amount)
        period_start = period_start or dt.date.today()
        period_end = period_end or add_months(period_start, 1) - dt.timedelta(days=1)

    discount_amount = money(discount)
    total = money(subtotal - discount_amount)
    if total < 0:
        total = Decimal("0.00")

    invoice = Invoice(
        invoice_number=await _unique_invoice_number(db),
        athlete_id=athlete.id,
        fee_plan_id=fee_plan_id,
        group_id=(await db.get(FeePlan, fee_plan_id)).group_id if fee_plan_id else None,
        period_start=period_start,
        period_end=period_end,
        issue_date=dt.date.today(),
        due_date=due_date or period_start,
        subtotal=money(subtotal),
        discount=discount_amount,
        total=total,
        paid=Decimal("0.00"),
        installment_no=installment_no,
        is_installment=is_installment,
        status=InvoiceStatus.UNPAID,
        notes=notes,
        is_active=True,
        club_id=athlete.club_id,
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def _sibling_discount(db: AsyncSession, athlete: Athlete, plan: FeePlan) -> Decimal:
    """حساب خصم الإخوة إن كان للرياضي أشقاء مسجّلون في نفس الخطة."""
    if not plan.sibling_discount:
        return Decimal("0")

    from app.models import GroupAthlete, ParentLink

    # Athletic siblings: رياضيون في نفس الفوج أو مرتبطون بنفس الولي
    result = await db.execute(
        select(func.count(Athlete.id))
        .join(GroupAthlete, GroupAthlete.athlete_id == Athlete.id)
        .where(
            GroupAthlete.group_id.in_(
                select(GroupAthlete.group_id).where(
                    GroupAthlete.athlete_id == athlete.id
                )
            ),
            Athlete.id != athlete.id,
            Athlete.is_active.is_(True),
        )
    )
    if (result.scalar() or 0) == 0:
        return Decimal("0")

    return plan.sibling_discount


async def create_subscription_invoices(
    db: AsyncSession, athlete_id: int, fee_plan_id: int
) -> list[Invoice]:
    """
    إصدار كل أقساط اشتراك لرياضي.

    الاشتراك السنوي بأقساط = 4 فواتير شهرية، кажд منها مستقلة في التتبع
    والتحصيل -- وهذا ما يفعله Finanças CLI في الواقع.
    """
    athlete = await safe_get(db, Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    plan = await get_fee_plan(db, fee_plan_id)
    installments = plan.installments
    if installments <= 1:
        return [await create_invoice(db, athlete_id=athlete_id, fee_plan_id=fee_plan_id)]

    invoices: list[Invoice] = []
    base = dt.date.today()
    months_per_installment = max(1, PERIOD_MONTHS.get(plan.period, 12) // installments)

    for i in range(installments):
        start = add_months(base, months_per_installment * i)
        end = add_months(start, months_per_installment) - dt.timedelta(days=1)
        invoice = await create_invoice(
            db,
            athlete_id=athlete_id,
            fee_plan_id=fee_plan_id,
            period_start=start,
            period_end=end,
            due_date=start,
        )
        invoice.installment_no = i + 1
        invoices.append(invoice)

    return invoices


async def get_invoice(db: AsyncSession, invoice_id: int) -> Invoice:
    """جلب فاتورة بمعرّفها."""
    invoice = await safe_get(db, Invoice, invoice_id)
    if invoice is None:
        raise NotFoundError("الفاتورة غير موجودة")
    return invoice


async def list_invoices(
    db: AsyncSession,
    *,
    athlete_id: int | None = None,
    status: str | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    overdue_only: bool = False,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Invoice], int]:
    """استعلام عن الفواتير مع ترقيم."""
    conditions = []

    if athlete_id:
        conditions.append(Invoice.athlete_id == athlete_id)
    if status:
        conditions.append(Invoice.status == status)
    if start_date:
        conditions.append(Invoice.issue_date >= start_date)
    if end_date:
        conditions.append(Invoice.issue_date <= end_date)
    if overdue_only:
        conditions.append(
            Invoice.due_date < dt.date.today(),
            Invoice.status.in_([
                InvoiceStatus.UNPAID,
                InvoiceStatus.PARTIAL,
                InvoiceStatus.OVERDUE,
            ]),
        )

    count_stmt = select(func.count(Invoice.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


async def cancel_invoice(db: AsyncSession, invoice_id: int, reason: str) -> Invoice:
    """إلغاء فاتورة مع تسجيل السبب."""
    invoice = await get_invoice(db, invoice_id)

    if invoice.status == InvoiceStatus.PAID:
        raise BusinessRuleError(
            "لا يمكن إلغاء فاتورة مدفوعة. يرجى إصدار دفعة استرداد بدلاً من ذلك"
        )

    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancel_reason = reason
    invoice.is_active = False
    await db.flush()
    return invoice


# =====================================================================
#  الدفعات
# =====================================================================
async def _unique_receipt_number(db: AsyncSession) -> str:
    for _ in range(20):
        number = generate_receipt_number()
        exists = await db.execute(
            select(Payment.id).where(Payment.receipt_number == number).limit(1)
        )
        if exists.scalar_one_or_none() is None:
            return number
    raise ConflictError("تعذّر توليد رقم وصل فريد")


async def record_payment(
    db: AsyncSession,
    *,
    invoice_id: int | None = None,
    athlete_id: int | None = None,
    amount: float,
    method: str = PaymentMethod.CASH.value,
    reference: str | None = None,
    notes: str | None = None,
    paid_at: dt.datetime | None = None,
    recorded_by_id: int | None = None,
) -> Payment:
    """
    تسجيل دفعة مالية.

    إذا لم تُحدد الفاتورة، يُبحث تلقائياً عن أقدم فاتورة غير مسددة للرياضي
    -- وهو السلوك المتوقع من موظف الاستقبال عند تسجيل دفعة شهرية سريعة.
    """
    amount = money(amount)
    if amount <= 0:
        raise ValidationError("مبلغ الدفعة يجب أن يكون أكبر من صفر")

    invoice: Invoice | None = None

    if invoice_id:
        invoice = await get_invoice(db, invoice_id)
    elif athlete_id:
        athlete = await safe_get(db, Athlete, athlete_id)
        if athlete is None:
            raise NotFoundError("الرياضي غير موجود")

        # أقدم فاتورة غير مسددة
        result = await db.execute(
            select(Invoice)
            .where(
                Invoice.athlete_id == athlete_id,
                Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
                Invoice.is_active.is_(True),
            )
            .order_by(Invoice.due_date)
            .limit(1)
        )
        invoice = result.scalar_one_or_none()

        if invoice is None:
            raise BusinessRuleError("لا توجد فواتير غير مسددة لهذا الرياضي")
    else:
        raise ValidationError("يجب تحديد الفاتورة أو الرياضي")

    due = invoice.amount_due
    if amount > due:
        raise BusinessRuleError(
            f"مبلغ الدفعة ({amount}) أكبر من المتبقي على الفاتورة ({due}). "
            "يُرجى تصحيح المبلغ"
        )

    payment = Payment(
        receipt_number=await _unique_receipt_number(db),
        invoice_id=invoice.id,
        athlete_id=invoice.athlete_id,
        amount=amount,
        paid_at=paid_at or utcnow(),
        payment_date=(paid_at or utcnow()).date(),
        method=method,
        status=PaymentStatus.COMPLETED,
        reference=reference,
        notes=notes,
        recorded_by_id=recorded_by_id,
        club_id=invoice.club_id,
    )
    db.add(payment)
    await db.flush()

    # ---- تحديث الفاتورة ----
    invoice.paid = money(Decimal(str(invoice.paid)) + amount)
    invoice.recalculate_status()
    await db.flush()

    return payment


async def refund_payment(db: AsyncSession, payment_id: int, reason: str) -> Payment:
    """استرداد دفعة كاملة أو جزئية."""
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise NotFoundError("الدفعة غير موجودة")

    if payment.status == PaymentStatus.REFUNDED:
        raise ConflictError("هذه الدفعة مستردة مسبقاً")

    if payment.invoice_id:
        invoice = await get_invoice(db, payment.invoice_id)
        invoice.paid = money(max(Decimal("0"), Decimal(str(invoice.paid)) - money(payment.amount)))
        invoice.recalculate_status()

    payment.status = PaymentStatus.REFUNDED
    payment.notes = f"{payment.notes or ''} | استرداد: {reason}".strip()
    await db.flush()
    return payment


async def list_payments(
    db: AsyncSession,
    *,
    athlete_id: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    method: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Payment], int]:
    """استعلام عن الدفعات."""
    conditions = []
    if athlete_id:
        conditions.append(Payment.athlete_id == athlete_id)
    if start_date:
        conditions.append(Payment.payment_date >= start_date)
    if end_date:
        conditions.append(Payment.payment_date <= end_date)
    if method:
        conditions.append(Payment.method == method)

    count_stmt = select(func.count(Payment.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Payment).order_by(Payment.payment_date.desc(), Payment.id.desc())
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


# =====================================================================
#  المصاريف
# =====================================================================
async def create_expense(db: AsyncSession, data: dict[str, Any]) -> Expense:
    """تسجيل مصروف."""
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError("وصف المصروف مطلوب")

    amount = money(data.get("amount") or 0)
    if amount <= 0:
        raise ValidationError("مبلغ المصروف يجب أن يكون أكبر من صفر")

    expense_date = data.get("expense_date")
    if isinstance(expense_date, str):
        expense_date = dt.date.fromisoformat(expense_date)

    expense = Expense(
        title=title,
        description=data.get("description"),
        category=data.get("category", ExpenseCategory.OTHER.value),
        amount=amount,
        expense_date=expense_date or dt.date.today(),
        staff_id=data.get("staff_id"),
        group_id=data.get("group_id"),
        receipt_url=data.get("receipt_url"),
        vendor=data.get("vendor"),
        reference=data.get("reference"),
        approved=data.get("approved", True),
        approved_by_id=data.get("approved_by_id"),
        approved_at=utcnow() if data.get("approved", True) else None,
        notes=data.get("notes"),
        recorded_by_id=data.get("recorded_by_id"),
        club_id=data.get("club_id"),
    )
    db.add(expense)
    await db.flush()
    return expense


async def list_expenses(
    db: AsyncSession,
    *,
    category: str | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Expense], int]:
    """استعلام عن المصاريف."""
    conditions = []
    if category:
        conditions.append(Expense.category == category)
    if start_date:
        conditions.append(Expense.expense_date >= start_date)
    if end_date:
        conditions.append(Expense.expense_date <= end_date)

    count_stmt = select(func.count(Expense.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc())
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


# =====================================================================
#  التقارير المالية
# =====================================================================
async def financial_summary(
    db: AsyncSession,
    *,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> dict[str, Any]:
    """
    ملخص مالي شامل للنادي.

    يشمل: الإيرادات، المصاريف، الصافي، الديون المستحقة، ونسبة التحصيل.
    """
    start_date = start_date or (dt.date.today() - dt.timedelta(days=30))
    end_date = end_date or dt.date.today()

    # --- الإيرادات ---
    income = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
        ).where(
            Payment.payment_date >= start_date,
            Payment.payment_date <= end_date,
            Payment.status == PaymentStatus.COMPLETED,
        )
    )
    income_row = income.first()
    total_income = money(income_row[0] if income_row else 0)
    payment_count = income_row[1] if income_row else 0

    # --- المصاريف ---
    expenses = await db.execute(
        select(
            func.coalesce(func.sum(Expense.amount), 0),
            func.count(Expense.id),
        ).where(
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date,
            Expense.approved.is_(True),
        )
    )
    expense_row = expenses.first()
    total_expenses = money(expense_row[0] if expense_row else 0)
    expense_count = expense_row[1] if expense_row else 0

    # --- الفواتير ---
    invoice_stats = await db.execute(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
            func.coalesce(func.sum(Invoice.paid), 0),
        ).where(Invoice.issue_date >= start_date, Invoice.issue_date <= end_date)
    )
    inv_row = invoice_stats.first()
    invoice_count = inv_row[0] if inv_row else 0
    invoiced_total = money(inv_row[1] if inv_row else 0)
    collected_total = money(inv_row[2] if inv_row else 0)

    # --- الديون الحالية (كل الفترات) ---
    debt = await db.execute(
        select(func.coalesce(func.sum(Invoice.total - Invoice.paid), 0)).where(
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
            Invoice.is_active.is_(True),
        )
    )
    outstanding = money(debt.scalar() or 0)

    # --- الفواتير المتأخرة ---
    overdue = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.OVERDUE, InvoiceStatus.PARTIAL]),
            Invoice.due_date < dt.date.today(),
        )
    )
    overdue_count = overdue.scalar() or 0

    # --- نسبة التحصيل ---
    collection_rate = (
        round((float(collected_total) / float(invoiced_total)) * 100, 1)
        if invoiced_total and float(invoiced_total) > 0
        else 0.0
    )

    return {
        "period": {"start": start_date, "end": end_date},
        "income": total_income,
        "payment_count": payment_count,
        "expenses": total_expenses,
        "expense_count": expense_count,
        "net": money(total_income - total_expenses),
        "invoiced_total": invoiced_total,
        "collected_total": collected_total,
        "invoice_count": invoice_count,
        "outstanding": outstanding,
        "overdue_count": overdue_count,
        "collection_rate": collection_rate,
    }


async def revenue_by_month(
    db: AsyncSession, months: int = 12
) -> list[dict[str, Any]]:
    """
    الإيرادات والمصاريف شهر بشهر لرسم بياني.

    نستخدم دوال تاريخ قاعدة البيانات بدلاً من Python حتى يبقى الاستعلام
    فعّالاً مع فهارس على ``payment_date``.
    """
    from sqlalchemy import case, cast, extract

    start = add_months(dt.date.today().replace(day=1), -(months - 1))

    income_rows = await db.execute(
        select(
            extract("year", Payment.payment_date).label("year"),
            extract("month", Payment.payment_date).label("month"),
            func.sum(Payment.amount),
        )
        .where(
            Payment.payment_date >= start,
            Payment.status == PaymentStatus.COMPLETED,
        )
        .group_by("year", "month")
    )
    income = {(int(r[0]), int(r[1])): money(r[2]) for r in income_rows.all()}

    expense_rows = await db.execute(
        select(
            extract("year", Expense.expense_date).label("year"),
            extract("month", Expense.expense_date).label("month"),
            func.sum(Expense.amount),
        )
        .where(Expense.expense_date >= start, Expense.approved.is_(True))
        .group_by("year", "month")
    )
    expenses = {(int(r[0]), int(r[1])): money(r[2]) for r in expense_rows.all()}

    from app.core.config import settings as _settings

    result = []
    for i in range(months):
        current = add_months(start, i)
        key = (current.year, current.month)
        inc = income.get(key, Decimal("0.00"))
        exp = expenses.get(key, Decimal("0.00"))
        result.append({
            "month": current.strftime("%Y-%m"),
            "label": current.strftime("%m/%Y"),
            "income": inc,
            "expenses": exp,
            "net": money(inc - exp),
        })
    return result


async def revenue_by_category(db: AsyncSession) -> list[dict[str, Any]]:
    """توزيع المصاريف حسب التصنيف (لفحص الدائرة في التقارير)."""
    rows = await db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.approved.is_(True))
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )

    labels = {
        "rent": "إيجار", "salary": "رواتب", "equipment": "تجهيزات",
        "maintenance": "صيانة", "utilities": "مرافق", "marketing": "تسويق",
        "transport": "نقل", "medical": "طبي", "other": "أخرى",
    }
    return [
        {
            "category": row[0],
            "label": labels.get(row[0], row[0]),
            "total": money(row[1]),
        }
        for row in rows.all()
    ]


async def debtors_report(
    db: AsyncSession, limit: int = 50
) -> list[dict[str, Any]]:
    """
    تقرير المدينين: الرياضيون عليهم مبالغ غير مسددة.

    مرتّب حسب المبلغ الأكبر أولاً، لأن هذا هو ترتيب المتابعة.
    """
    rows = await db.execute(
        select(
            Athlete.id,
            Athlete.full_name,
            Athlete.phone,
            Athlete.guardian_name,
            Athlete.guardian_phone,
            func.sum(Invoice.total - Invoice.paid).label("debt"),
            func.count(Invoice.id).label("invoice_count"),
            func.min(Invoice.due_date).label("oldest_due"),
        )
        .join(Invoice, Invoice.athlete_id == Athlete.id)
        .where(
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
            Invoice.is_active.is_(True),
        )
        .group_by(Athlete.id)
        .having(func.sum(Invoice.total - Invoice.paid) > 0)
        .order_by(func.sum(Invoice.total - Invoice.paid).desc())
        .limit(limit)
    )

    return [
        {
            "athlete_id": row[0],
            "full_name": row[1],
            "phone": row[2],
            "guardian_name": row[3],
            "guardian_phone": row[4],
            "debt": money(row[5]),
            "invoice_count": row[6],
            "oldest_due": row[7],
            "days_overdue": (dt.date.today() - row[7]).days if row[7] else 0,
        }
        for row in rows.all()
    ]