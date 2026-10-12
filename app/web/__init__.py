"""
موجّه مسارات الواجهة (Web Router Aggregator)
============================================
يجمع كل مسارات الصفحات في موجّه واحد يُربط بـ ``app.main``.
"""

from fastapi import APIRouter

from app.web.routes import auth_router, public_router
from app.web.routes_dashboard import club_router, profile_router
from app.web.routes_portals import admin_router, coach_router, parent_router

router = APIRouter(include_in_schema=False)

# --- المصادقة والبوابة العامة (متاحة للجميع) ---
router.include_router(auth_router)
router.include_router(public_router)

# --- بوابات العمل (محمية بجلسات + أدوار) ---
router.include_router(club_router)
router.include_router(coach_router)
router.include_router(parent_router)
router.include_router(admin_router)
router.include_router(profile_router)

__all__ = ["router"]