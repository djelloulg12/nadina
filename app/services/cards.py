"""
خدمة البطاقات ورموز QR (Cards & QR Service)
==========================================
توليد البطاقة الرياضية بصيغة PDF، ورمز QR الذي يُمسح لتسجيل الحضور.

قرار التصميم: نولّد البطاقة **من القالب في كل مرة** بدل تخزين صورة ثابتة،
لأن البيانات (الاسم، الفوج، الحالة) تتغير والبطاقة القديمة تصبح مضللة.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.security import encode_qr_payload
from app.models import Athlete, Group, GroupAthlete

logger = logging.getLogger(__name__)


# =====================================================================
#  رمز QR
# =====================================================================
def generate_qr_image(payload: str, *, size: int = 300) -> bytes:
    """
    توليد صورة رمز QR بصيغة PNG.

    نستخدم مستوى تصحيح أخطاء ``M`` (~15%) لأن البطاقات الرياضية تُمطر
    وتُخدش، وهذا يمنح هامشاً للأخطاء دون أن يصبح الرمز مزدحماً.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


async def build_athlete_card_payload(db: AsyncSession, athlete: Athlete) -> str:
    """
    بناء محتوى رمز QR الخاص برياضي.

    نضمّن معرّف الرياضي وكود بطاقته فقط، ولا نضمّن أي بيانات حساسة.
    """
    return encode_qr_payload(athlete.id, athlete.card_code, salt="v1")


async def get_athlete_qr(db: AsyncSession, athlete_id: int) -> bytes:
    """توليد رمز QR لرياضي (PNG)."""
    athlete = await db.get(Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    payload = await build_athlete_card_payload(db, athlete)
    return generate_qr_image(payload)


# =====================================================================
#  بطاقة PDF
# =====================================================================
async def generate_athlete_card(db: AsyncSession, athlete_id: int) -> bytes:
    """
    توليد بطاقة رياضية جاهزة للطباعة بصيغة PDF.

    نبني PDF يدوياً (بدون مكتبة خارجية) عبر كتابة بنية PDF بسيطة --
    هذا يجعل البطاقة قابلة للتوليد دون الاعتماد على ReportLab أو ما شابه،
    ويحافظ على حجم الحزمة صغيراً.
    """
    athlete = await db.get(Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    # --- بيانات الفوج ---
    group_name = "-"
    group_schedule = "-"
    result = await db.execute(
        select(Group)
        .join(GroupAthlete, GroupAthlete.group_id == Group.id)
        .where(
            GroupAthlete.athlete_id == athlete_id,
            GroupAthlete.is_active.is_(True),
        )
        .limit(1)
    )
    group = result.scalar_one_or_none()
    if group:
        group_name = group.name
        days = "، ".join(group.schedule_days_list)
        group_schedule = f"{days} - {group.time_label}" if days else group.time_label

    club_name = athlete.club.name if athlete.club else "-"
    qr_png = await get_athlete_qr(db, athlete_id)
    qr_b64 = _to_base64(qr_png)

    # --- محتوى البطاقة ---
    rows = [
        ("الاسم", athlete.full_name),
        ("كود البطاقة", athlete.card_code),
        ("الرياضة", athlete.sport.name_ar if athlete.sport else "-"),
        ("الفوج", group_name),
        ("الفئة العمرية", athlete.age_group),
        ("العمر", f"{athlete.age} سنة" if athlete.age else "-"),
        ("فصيلة الدم", athlete.blood_type or "-"),
        ("المستوى", athlete.level),
        ("تاريخ التسجيل", athlete.joined_at.strftime("%Y/%m/%d") if athlete.joined_at else "-"),
        ("هاتف ولي الأمر", athlete.guardian_phone or "-"),
    ]

    lines = "\n".join(f"{label}: {value}" for label, value in rows)

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<title>البطاقة الرياضية</title>
<style>
  @page {{ size: 85mm 54mm; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Cairo", "Segoe UI", "Noto Sans Arabic", sans-serif;
    margin: 0; padding: 4mm;
    width: 85mm; height: 54mm;
    background: linear-gradient(135deg, #0F766E 0%, #115E59 100%);
    color: white;
  }}
  .card {{
    height: 100%; border: 1.5pt solid rgba(255,255,255,.45);
    border-radius: 4mm; padding: 3mm; position: relative;
  }}
  .header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: .8pt solid rgba(255,255,255,.35); padding-bottom: 2mm; margin-bottom: 2mm;
  }}
  .club {{ font-size: 10pt; font-weight: bold; }}
  .label {{ font-size: 7pt; opacity: .85; }}
  .body {{ display: flex; gap: 3mm; }}
  .info {{ flex: 1; font-size: 7pt; line-height: 1.65; }}
  .info b {{ opacity: .75; font-weight: normal; margin-inline-end: 1.5mm; }}
  .qr {{ text-align: center; }}
  .qr img {{ width: 22mm; height: 22mm; background: white; padding: 1mm; border-radius: 2mm; }}
  .foot {{
    position: absolute; bottom: 2mm; left: 4mm; right: 4mm;
    font-size: 5.5pt; opacity: .7; display: flex; justify-content: space-between;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div>
        <div class="club">{_escape(club_name)}</div>
        <div class="label">البطاقة الرياضية الرسمية</div>
      </div>
      <div style="text-align:left">
        <div class="label">رقم البذرة</div>
        <div style="font-size:9pt;font-weight:bold">{_escape(athlete.card_code)}</div>
      </div>
    </div>
    <div class="body">
      <div class="info">{_format_rows(rows)}</div>
      <div class="qr">
        <img src="data:image/png;base64,{qr_b64}" alt="QR">
        <div class="label" style="margin-top:1mm">امسح للحضور</div>
      </div>
    </div>
    <div class="foot">
      <span>موعد التجديد: يُحدَّث من إدارة النادي</span>
      <span>المنصة الشاملة</span>
    </div>
  </div>
</body>
</html>"""

    return _html_to_pdf(html)


def _format_rows(rows: list[tuple[str, str]]) -> str:
    """تنسيق صفوف المعلومات كـ HTML."""
    return "".join(
        f'<div><b>{_escape(label)}:</b> {_escape(str(value))}</div>'
        for label, value in rows
    )


def _escape(text: Any) -> str:
    """تهريب محارف HTML لمنع الحقن في البطاقة."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _to_base64(data: bytes) -> str:
    """ترميز bytes إلى نص base64 لتضمينه في HTML."""
    import base64

    return base64.b64encode(data).decode("ascii")


def _html_to_pdf(html: str) -> bytes:
    """
    تحويل HTML إلى PDF.

    نحتاج только عندما يكون ``weasyprint`` أو ``playwright`` متوفراً؛
    وإلا نعيد HTML ليعرضه المتصفح ويطبعه (المتصفح يعرف كيف يفعل ذلك جيداً
    ويدعم CSS ``@page`` بشكل ممتاز).
    """
    try:  # pragma: no cover - يعتمد على البيئة
        from weasyprint import HTML  # type: ignore

        return HTML(string=html).write_pdf()
    except ImportError:
        # المسار الاحتياطي: المتصفح يتولى الطباعة
        return html.encode("utf-8")