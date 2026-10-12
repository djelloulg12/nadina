"""
موجّه API الإصدار الأول (/api/v1)
=================================
يجمع كل المسارات في نقطة دخول واحدة.

**طبقتان من الحماية** تُطبَّقان معاً:

1. **حارس الرتبة على مستوى الموجّه** (``dependencies=[...]``): يمنع
   الرتب غير المناسبة أصلاً من الوصول لمسارات إدارة النادي أو مساحة
   المدرب. هذا ضروري حتى لو امتلك الدور صلاحية granular عابرة --
   فمسار ``/club/athletes`` ليس مكاناً لولي أمر ولو قرأ صلاحية
   ``athletes.read``.
2. **الصلاحية الدقيقة لكل مسار** (``require_permission``): تحدد ما
   يمكن لكل دور فعله داخل المسار.

طبقة الصلاحيات وحدها ليست كافية، وطبقة الرتب وحدها ليست دقيقة بما يكفي.
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_roles
from app.api.v1 import admin, auth, club, coach, parent, public
from app.models import UserRole

api_router = APIRouter(prefix="/api/v1")

# --- المصادقة (كلمة واحدة لكل المسارات) ---
api_router.include_router(auth.router)

# --- البوابة العامة (بدون مصادقة) ---
api_router.include_router(public.router)

# --- إدارة النادي: لطاقم النادي فقط ---
api_router.include_router(
    club.router,
    dependencies=[Depends(require_roles(
        UserRole.CLUB_ADMIN, UserRole.RECEPTIONIST, UserRole.COACH
    ))],
)

# --- مساحة المدرب: للمدربين فقط ---
api_router.include_router(
    coach.router,
    dependencies=[Depends(require_roles(UserRole.COACH))],
)

# --- بوابة الولي: للوالين والرياضيين فقط ---
api_router.include_router(
    parent.router,
    dependencies=[Depends(require_roles(UserRole.PARENT, UserRole.ATHLETE))],
)

# --- الإشعارات ولوحة المنصة ---
api_router.include_router(admin.router)
api_router.include_router(
    admin.platform_router,
    dependencies=[Depends(require_roles(UserRole.SUPER_ADMIN))],
)

__all__ = ["api_router"]