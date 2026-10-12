"""
خدمة الاستيراد والتصدير (Excel Import/Export Service)
===================================================
استيراد قوائم الرياضيين من Excel، وتصدير القوائم والبيانات المالية.

هذه الخدمة تعالج الملف **صفاً صفاً** مع تقرير أخطاء مفصّل، لأن الاستيراد
الجزئي (نجاح بعض الصفوف وفشل أخرى) هو السلوك الواقعي: النموذج المُرفع
من clubs عادةً يحتوي على أخطاء يدخلها موظف الاستقبال.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import generate_card_code
from app.models import Athlete, Group, GroupAthlete, Status

logger = logging.getLogger(__name__)


# =====================================================================
#  تعريف الأعمدة
# =====================================================================
#: أعمدة استيراد الرياضيين بالترتيب + تسميات عربية
IMPORT_COLUMNS: list[tuple[str, str]] = [
    ("last_name", "اسم العائلة"),
    ("first_name", "الاسم الأول"),
    ("birth_date", "تاريخ الميلاد"),
    ("gender", "الجنس"),
    ("phone", "الهاتف"),
    ("email", "البريد الإلكتروني"),
    ("national_id", "رقم الهوية"),
    ("blood_type", "فصيلة الدم"),
    ("guardian_name", "اسم ولي الأمر"),
    ("guardian_phone", "هاتف ولي الأمر"),
    ("address", "العنوان"),
    ("wilaya", "الولاية"),
    ("sport", "الرياضة"),
    ("group", "الفوج"),
    ("level", "المستوى"),
    ("nationality", "الجنسية"),
    ("medical_notes", "ملاحظات طبية"),
]

#: ترجمة القيم النصية العربية إلى القيم المخزّنة
VALUE_MAPS: dict[str, dict[str, str]] = {
    "gender": {
        "ذكر": "male",
        "male": "male",
        "m": "male",
        "أنثى": "female",
        "female": "female",
        "f": "female",
    },
    "level": {
        "مبتدئ": "beginner",
        "beginner": "beginner",
        "هاوٍ": "amateur",
        "هاوي": "amateur",
        "amateur": "amateur",
        "متقدم": "advanced",
        "advanced": "advanced",
        "نخبة": "elite",
        "محترف": "elite",
        "elite": "elite",
    },
}


# =====================================================================
#  أدوات التنسيق
# =====================================================================
HEADER_FILL = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14, color="0F766E")
THIN_BORDER = Border(
    left=Side(style="thin", color="D1D5DB"),
    right=Side(style="thin", color="D1D5DB"),
    top=Side(style="thin", color="D1D5DB"),
    bottom=Side(style="thin", color="D1D5DB"),
)
MONEY_FORMAT = '#,##0.00'


def _style_header(worksheet, columns_count: int, row: int = 1) -> None:
    """تنسيق صف العناوين."""
    for col in range(1, columns_count + 1):
        cell = worksheet.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER


def _auto_width(worksheet, min_width: int = 12, max_width: int = 40) -> None:
    """ضبط عرض الأعمدة تلقائياً حسب المحتوى."""
    for column_cells in worksheet.columns:
        length = 0
        for cell in column_cells:
            if cell.value is not None:
                length = max(length, len(str(cell.value)))
        index = column_cells[0].column
        worksheet.column_dimensions[get_column_letter(index)].width = min(
            max(length + 4, min_width), max_width
        )


def _parse_date(value: Any) -> dt.date | None:
    """تحويل قيمة خلية إلى تاريخ، مع دعم عدة صيغ."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalise(value: Any) -> str:
    """تنظيف قيمة خلية."""
    if value is None:
        return ""
    return str(value).strip()


# =====================================================================
#  قالب الاستيراد
# =====================================================================
def build_import_template() -> bytes:
    """
    توليد ملف Excel فارغ كقالب للاستيراد.

    نضيف صف مثال واحد لأن المستخدمين يحتاجون مرجعاً للصيغة، وليس
    لأننا نتوقع منهم تخمين تاريخ الميلاد.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "الرياضيون"

    headers = [label for _, label in IMPORT_COLUMNS]
    ws.append(headers)
    _style_header(ws, len(headers))

    example = [
        "بن سالم", "أحمد", "2012-05-14", "ذكر", "0555123456",
        "", "123456789012", "O+", "محمد بن سالم", "0555987654",
        "حي النصر", "الجزائر", "كرة القدم", "فوج الصغار",
        "مبتدئ", "جزائري", "",
    ]
    ws.append(example)

    # ورقة التعليمات
    info = wb.create_sheet("التعليمات")
    info["A1"] = "تعليمات استيراد الرياضيين"
    info["A1"].font = TITLE_FONT
    lines = [
        "",
        "1. لا تغيّر أسماء الأعمدة في الصف الأول.",
        "2. تاريخ الميلاد بصيغة: سنة/شهر/يوم (مثال: 2012-05-14).",
        "3. الجنس: ذكر أو أنثى.",
        "4. المستوى: مبتدئ، هاوٍ، متقدم، أو نخبة.",
        "5. عمود الفوج اختياري -- سيُنشأ إن لم يوجد.",
        "6. الصف الذي يحتوي خطأ سيُتخطى، وستجد تقرير الأخطاء في ملف النتيجة.",
        "",
        "⚠️ لا تكرر كود البطاقة (إن وُجد) -- النظام يولّده تلقائياً.",
    ]
    for i, line in enumerate(lines, start=2):
        info.cell(row=i, column=1, value=line)

    info.column_dimensions["A"].width = 70
    _auto_width(ws)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# =====================================================================
#  استيراد الرياضيين
# =====================================================================
async def import_athletes(
    db: AsyncSession,
    file_content: bytes,
    *,
    default_group_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    استيراد قائمة رياضيين من ملف Excel.

    ``dry_run=True`` يحاكي العملية دون حفظ -- مفيد جداً للتأكد من
    صحة الملف قبل تطبيقه على قاعدة البيانات الحقيقية.
    """
    try:
        wb = load_workbook(io.BytesIO(file_content), data_only=True)
    except Exception as exc:
        raise ValidationError(
            "تعذّر قراءة الملف. تأكد أنه بصيغة Excel (.xlsx) غير تالف"
        ) from exc

    ws = wb.active
    if ws.max_row < 2:
        raise ValidationError("الملف فارغ أو لا يحتوي على بيانات")

    # --- خريطة الأعمدة: العناوين العربية -> المفاتيح الداخلية ---
    headers = [_normalise(c.value) for c in ws[1]]
    column_map: dict[str, int] = {}
    for key, label in IMPORT_COLUMNS:
        for idx, header in enumerate(headers):
            if header == label:
                column_map[key] = idx
                break

    if "first_name" not in column_map or "last_name" not in column_map:
        raise ValidationError(
            "الملف يجب أن يحتوي على عمودَي «الاسم الأول» و«اسم العائلة» "
            "في الصف الأول. يمكنك تحميل القالب الجاهز"
        )

    # --- قيم مرجعية ---
    groups = await _load_groups(db)
    sports = await _load_sports(db)

    imported: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped = 0

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if all(v is None or _normalise(v) == "" for v in row):
            continue

        def cell(key: str) -> Any:
            idx = column_map.get(key)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        first_name = _normalise(cell("first_name"))
        last_name = _normalise(cell("last_name"))

        if not first_name and not last_name:
            errors.append({
                "row": row_idx,
                "name": "-",
                "error": "الاسم مطلوب",
            })
            continue

        # --- تحقق من الاسم ---
        if not first_name or not last_name:
            errors.append({
                "row": row_idx,
                "name": f"{first_name} {last_name}".strip(),
                "error": "يجب إدخال الاسم الأول واسم العائلة معاً",
            })
            continue

        full_name = f"{first_name} {last_name}"

        # --- تحقق من التكرار داخل الملف ---
        if any(item["full_name"] == full_name for item in imported):
            skipped += 1
            continue

        # --- تاريخ الميلاد ---
        birth_date = _parse_date(cell("birth_date"))
        if cell("birth_date") and birth_date is None:
            errors.append({
                "row": row_idx, "name": full_name,
                "error": "تاريخ الميلاد غير صالح (الصيغة المطلوبة: سنة/شهر/يوم)",
            })
            continue

        # --- الجنس ---
        gender_raw = _normalise(cell("gender")).lower()
        gender = VALUE_MAPS["gender"].get(gender_raw) or (gender_raw if gender_raw in ("male", "female") else None)

        # --- المستوى ---
        level_raw = _normalise(cell("level")).lower()
        level = VALUE_MAPS["level"].get(level_raw, "beginner")

        # --- الرياضة ---
        sport_id = None
        sport_name = _normalise(cell("sport"))
        if sport_name and sports:
            sport_id = sports.get(sport_name)

        # --- الفوج ---
        group_id = default_group_id
        group_name = _normalise(cell("group"))
        if group_name and groups:
            group_id = groups.get(group_name)

        record = {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "birth_date": birth_date,
            "gender": gender,
            "phone": _normalise(cell("phone")) or None,
            "email": _normalise(cell("email")) or None,
            "national_id": _normalise(cell("national_id")) or None,
            "blood_type": _normalise(cell("blood_type")) or None,
            "guardian_name": _normalise(cell("guardian_name")) or None,
            "guardian_phone": _normalise(cell("guardian_phone")) or None,
            "address": _normalise(cell("address")) or None,
            "wilaya": _normalise(cell("wilaya")) or None,
            "sport_id": sport_id,
            "group_id": group_id,
            "level": level,
            "nationality": _normalise(cell("nationality")) or None,
            "medical_notes": _normalise(cell("medical_notes")) or None,
            "status": Status.ACTIVE,
            "joined_at": dt.date.today(),
        }
        imported.append(record)

    # --- وضع المحاكاة: لا نكتب شيئاً ---
    if dry_run:
        return {
            "success": not errors,
            "dry_run": True,
            "total_rows": len(imported) + len(errors) + skipped,
            "imported": len(imported),
            "skipped": skipped,
            "failed": len(errors),
            "errors": errors,
            "preview": imported[:20],
        }

    # --- الحفظ الفعلي ---
    created = 0
    for record in imported:
        try:
            athlete = Athlete(card_code=generate_card_code(), **record)
            db.add(athlete)
            await db.flush()

            if record.get("group_id"):
                db.add(
                    GroupAthlete(
                        club_id=athlete.club_id,
                        group_id=record["group_id"],
                        athlete_id=athlete.id,
                        joined_at=dt.date.today(),
                        is_active=True,
                    )
                )
                await db.flush()
                group = await db.get(Group, record["group_id"])
                if group:
                    group.current_count = (group.current_count or 0) + 1

            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "row": "-",
                "name": record["full_name"],
                "error": f"فشل الحفظ: {str(exc)[:150]}",
            })

    await db.flush()

    return {
        "success": not errors,
        "dry_run": False,
        "total_rows": len(imported) + len(errors) + skipped,
        "imported": created,
        "skipped": skipped,
        "failed": len(errors),
        "errors": errors,
    }


async def _load_groups(db: AsyncSession) -> dict[str, int]:
    """خريطة اسم الفوج -> معرّفه (للاستيراد)."""
    result = await db.execute(select(Group).where(Group.is_active.is_(True)))
    return {g.name: g.id for g in result.scalars().all()}


async def _load_sports(db: AsyncSession) -> dict[str, int]:
    """خريطة اسم الرياضة -> معرّفها."""
    from app.models import Sport

    result = await db.execute(select(Sport).where(Sport.is_active.is_(True)))
    return {s.name_ar: s.id for s in result.scalars().all()}


def build_import_errors_file(errors: list[dict[str, Any]]) -> bytes:
    """توليد ملف أخطاء الاستيراد لمشاركته مع المستخدم."""
    wb = Workbook()
    ws = wb.active
    ws.title = "الأخطاء"
    ws.append(["رقم السطر", "الاسم", "السبب"])
    _style_header(ws, 3)

    for err in errors:
        ws.append([err.get("row"), err.get("name"), err.get("error")])

    _auto_width(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# =====================================================================
#  التصدير
# =====================================================================
async def export_athletes(db: AsyncSession, athlete_ids: list[int] | None = None) -> bytes:
    """تصدير قائمة الرياضيين إلى Excel."""
    stmt = select(Athlete).where(Athlete.is_active.is_(True))
    if athlete_ids:
        stmt = stmt.where(Athlete.id.in_(athlete_ids))

    athletes = list((await db.execute(stmt.order_by(Athlete.full_name))).scalars().all())

    wb = Workbook()
    ws = wb.active
    ws.title = "الرياضيون"

    headers = [
        "كود البطاقة", "الاسم الكامل", "الاسم الأول", "اسم العائلة",
        "تاريخ الميلاد", "العمر", "الجنس", "الفئة العمرية",
        "الهاتف", "هاتف ولي الأمر", "الولاية",
        "الرياضة", "المستوى", "رقم القميص",
        "فصيلة الدم", "الحساسية", "الأمراض المزمنة",
        "تاريخ التسجيل", "الحالة",
    ]
    ws.append(headers)
    _style_header(ws, len(headers))

    for a in athletes:
        sport = a.sport.name_ar if a.sport else ""
        ws.append([
            a.card_code,
            a.full_name,
            a.first_name,
            a.last_name,
            a.birth_date,
            a.age,
            a.gender_label,
            a.age_group,
            a.phone or "",
            a.guardian_phone or "",
            a.wilaya or "",
            sport,
            a.level,
            a.jersey_number or "",
            a.blood_type or "",
            a.allergies or "",
            a.chronic_conditions or "",
            a.joined_at or "",
            a.status,
        ])

    _auto_width(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


async def export_payments(db: AsyncSession, start_date: dt.date, end_date: dt.date) -> bytes:
    """تصقرير دفعات مفصّل (المدفوعات فقط)."""
    from app.models import Payment

    stmt = (
        select(Payment, Athlete.full_name)
        .join(Athlete, Athlete.id == Payment.athlete_id)
        .where(
            Payment.payment_date >= start_date,
            Payment.payment_date <= end_date,
            Payment.status == "completed",
        )
        .order_by(Payment.payment_date.desc())
    )
    rows = (await db.execute(stmt)).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "الدفعات"

    ws.append([
        f"تقرير الدفعات من {start_date.strftime('%Y/%m/%d')}"
        f" إلى {end_date.strftime('%Y/%m/%d')}"
    ])
    ws["A1"].font = TITLE_FONT
    ws.append([])

    headers = ["رقم الوصل", "التاريخ", "الرياضي", "المبلغ", "وسيلة الدفع", "المرجع", "الحالة"]
    ws.append(headers)
    _style_header(ws, len(headers), row=3)

    total = 0.0
    for payment, athlete_name in rows:
        total += float(payment.amount)
        ws.append([
            payment.receipt_number,
            payment.payment_date,
            athlete_name,
            float(payment.amount),
            payment.method_label,
            payment.reference or "",
            payment.status,
        ])

    total_row = ws.max_row + 2
    ws.cell(row=total_row, column=3, value="الإجمالي").font = Font(bold=True)
    total_cell = ws.cell(row=total_row, column=4, value=total)
    total_cell.font = Font(bold=True)
    total_cell.number_format = MONEY_FORMAT

    # تنسيق أعمدة المبالغ
    for row in ws.iter_rows(min_row=4, max_row=ws.max_row, min_col=4, max_col=4):
        for cell in row:
            cell.number_format = MONEY_FORMAT

    _auto_width(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


async def export_attendance_roster(
    db: AsyncSession, session_id: int
) -> bytes:
    """
    كشف حضور حصة (القائمة الاسمية مع حالة كل رياضي).

    هذا الملف يُطبع ويُوقَّع من المدرب ويُسلَّم للإدارة -- الشكل يهم هنا
    أكثر من الدقة الرقمية.
    """
    from app.models import Attendance, TrainingSession

    session = await db.get(TrainingSession, session_id)
    if session is None:
        raise ValidationError("الحصة غير موجودة")

    group = await db.get(Group, session.group_id) if session.group_id else None

    rows_stmt = select(Attendance, Athlete).join(
        Athlete, Athlete.id == Attendance.athlete_id
    ).where(Attendance.session_id == session_id).order_by(Athlete.full_name)
    rows = (await db.execute(rows_stmt)).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "كشف الحضور"

    ws.merge_cells("A1:G1")
    ws["A1"] = "كشف حضور حصة تدريبية"
    ws["A1"].font = Font(bold=True, size=16, color="0F766E")
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A2:G2")
    ws["A2"] = (
        f"الفوج: {group.name if group else 'غير محدد'}    |    "
        f"التاريخ: {session.session_date.strftime('%Y/%m/%d')}    |    "
        f"التوقيت: {session.time_label}"
    )
    ws["A2"].alignment = Alignment(horizontal="center")

    headers = ["#", "كود البطاقة", "اسم الرياضي", "الحالة", "التأخير", "الملاحظات", "التوقيع"]
    ws.append([])
    ws.append(headers)
    _style_header(ws, len(headers), row=4)

    present = absent = 0
    for idx, (record, athlete) in enumerate(rows, start=1):
        if record.status in ("present", "late"):
            present += 1
        elif record.status == "absent":
            absent += 1
        ws.append([
            idx,
            athlete.card_code,
            athlete.full_name,
            record.status_label,
            f"{record.minutes_late} د" if record.minutes_late else "",
            record.notes or "",
            "",
        ])

    summary_row = ws.max_row + 2
    ws.cell(row=summary_row, column=2, value="الإجمالي").font = Font(bold=True)
    ws.cell(row=summary_row, column=3, value=f"{len(rows)} رياضي")
    ws.cell(row=summary_row, column=4, value=f"حاضر: {present} | غائب: {absent}")

    ws.cell(row=summary_row + 2, column=1, value="توقيع المدرب: ......................")
    ws.cell(row=summary_row + 2, column=4, value="توقيع المدير: ......................")

    _auto_width(ws)
    for col in range(1, 8):
        ws.column_dimensions[get_column_letter(col)].width = max(
            ws.column_dimensions[get_column_letter(col)].width or 12, 14
        )

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()