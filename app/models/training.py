"""
نماذج التدريب (Training Models)
===============================
الأفواج، الحصص التدريبية، الحضور، والتقييمات الفنية.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, SoftDeleteMixin, TenantMixin, utcnow
from app.models.enums import (
    AttendanceMethod,
    AttendanceStatus,
    EvaluationType,
    Level,
    SessionStatus,
    Status,
)

if TYPE_CHECKING:
    from app.models.people import Athlete, StaffMember
    from app.models.platform import Club, Sport, User


# =====================================================================
#  الأفواج (الفئات التدريبية)
# =====================================================================
class Group(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    فوج / فئة تدريبية داخل النادي.

    كل فوج ينتمي لنادٍ واحد، وله مدرب واحد أو أكثر، ويضم عدة رياضيين.
    """

    __tablename__ = "groups"

    #: للوالين: الأفواج التي ينتمي إليها أبناؤهم
    __parent_scope__ = "id:groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(
        String(30), index=True, comment="كود مختصر للأفواج"
    )

    # --- التصنيف ---
    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )
    level: Mapped[Level] = mapped_column(default=Level.BEGINNER, index=True)
    min_age: Mapped[int | None] = mapped_column(Integer, comment="أصغر عمر مقبول")
    max_age: Mapped[int | None] = mapped_column(Integer, comment="أكبر عمر مقبول")
    gender: Mapped[str | None] = mapped_column(String(10), index=True)

    # --- المدرب ---
    coach_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_members.id", ondelete="SET NULL"),
        index=True,
        comment="المدرب المسؤول",
    )

    # --- السعة والموقع ---
    capacity: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    current_count: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str | None] = mapped_column(String(20), comment="لون الفوج في التقويم")
    location: Mapped[str | None] = mapped_column(String(150))

    # --- الجدول ---
    #: أيام الأسبوع بصيغة نصية مفصولة بفواصل: "الأحد,الثلاثاء,الخميس"
    schedule_days: Mapped[str | None] = mapped_column(String(200))
    start_time: Mapped[dt.time | None] = mapped_column(Time)
    end_time: Mapped[dt.time | None] = mapped_column(Time)
    monthly_fee: Mapped[float | None] = mapped_column(
        Numeric(10, 2), comment="الاشتراك الشهري للفوج"
    )

    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(
        default=Status.ACTIVE, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # --- العلاقات ---
    club: Mapped["Club"] = relationship("Club", back_populates="groups", lazy="selectin")
    sport: Mapped["Sport | None"] = relationship("Sport", lazy="selectin")
    coach: Mapped["StaffMember | None"] = relationship(
        "StaffMember", back_populates="groups_coached", lazy="selectin"
    )
    athletes: Mapped[list["GroupAthlete"]] = relationship(
        "GroupAthlete", back_populates="group", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["TrainingSession"]] = relationship(
        "TrainingSession", back_populates="group", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_groups_club_status", "club_id", "status"),
        Index("ix_groups_club_coach", "club_id", "coach_id"),
        Index("ix_groups_name_search", "club_id", "name"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    # -----------------------------------------------------------------
    @property
    def is_full(self) -> bool:
        """هل الفوج مكتمل العدد؟"""
        return self.current_count >= self.capacity

    @property
    def available_slots(self) -> int:
        """المقاعد الشاغرة."""
        return max(0, self.capacity - self.current_count)

    @property
    def schedule_days_list(self) -> list[str]:
        """أيام التدريب كقائمة."""
        if not self.schedule_days:
            return []
        return [d.strip() for d in self.schedule_days.split(",") if d.strip()]

    @property
    def time_label(self) -> str:
        """توقيت الحصة بصيغة نصية."""
        if not self.start_time:
            return "غير محدد"
        end = self.end_time.strftime("%H:%M") if self.end_time else ""
        return f"{self.start_time.strftime('%H:%M')} - {end}" if end else self.start_time.strftime("%H:%M")

    def accepts_age(self, age: int | None) -> bool:
        """هل يقبل الفوج عمراً معيّناً؟"""
        if age is None:
            return False
        if self.min_age is not None and age < self.min_age:
            return False
        if self.max_age is not None and age > self.max_age:
            return False
        return True


# =====================================================================
#  ربط الرياضي بالفوج
# =====================================================================
class GroupAthlete(TenantMixin, BaseModel):
    """جدول ربط متعدد-متعدد بين الرياضيين والأفواج."""

    __tablename__ = "group_athletes"

    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    joined_at: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    left_at: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_captain: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="هل هو قائد الفوج؟"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    group: Mapped["Group"] = relationship("Group", back_populates="athletes")
    athlete: Mapped["Athlete"] = relationship(
        "Athlete", back_populates="groups", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("group_id", "athlete_id", name="uq_group_athlete"),
        Index("ix_group_athletes_athlete_active", "athlete_id", "is_active"),
        Index("ix_group_athletes_club", "club_id"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"فوج {self.group_id} <- رياضي {self.athlete_id}"


# =====================================================================
#  الحصص التدريبية
# =====================================================================
class TrainingSession(TenantMixin, BaseModel):
    """حصة تدريبية مجدولة."""

    __tablename__ = "training_sessions"

    __parent_scope__ = "group_id:groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    coach_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_members.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str | None] = mapped_column(String(200))
    session_date: Mapped[dt.date] = mapped_column(
        Date, nullable=False, index=True, comment="تاريخ الحصة"
    )
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)

    location: Mapped[str | None] = mapped_column(String(150))
    session_type: Mapped[str | None] = mapped_column(
        String(50), comment="تدريب / مباراة / اختبار"
    )
    objectives: Mapped[str | None] = mapped_column(Text, comment="أهداف الحصة")
    notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[SessionStatus] = mapped_column(
        default=SessionStatus.SCHEDULED, nullable=False, index=True
    )
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    #: عدد الرياضيين المسجلين في الحصة
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    #: عدد الحاضرين فعلياً (يُحدّث بعد تسجيل الحضور)
    present_count: Mapped[int] = mapped_column(Integer, default=0)
    absent_count: Mapped[int] = mapped_column(Integer, default=0)

    #: هل تم إرسال تذكير للوالين؟
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    club: Mapped["Club"] = relationship("Club", back_populates="sessions")
    group: Mapped["Group | None"] = relationship(
        "Group", back_populates="sessions"
    )
    coach: Mapped["StaffMember | None"] = relationship("StaffMember")
    attendance: Mapped[list["Attendance"]] = relationship(
        "Attendance", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_sessions_club_date", "club_id", "session_date"),
        Index("ix_sessions_coach_date", "coach_id", "session_date"),
        Index("ix_sessions_date_status", "session_date", "status"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"حصة {self.session_date} - {self.group_id}"

    # -----------------------------------------------------------------
    @property
    def is_today(self) -> bool:
        return self.session_date == dt.date.today()

    @property
    def is_past(self) -> bool:
        return self.session_date < dt.date.today()

    @property
    def time_label(self) -> str:
        return f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"

    @property
    def duration_minutes(self) -> int:
        """مدة الحصة بالدقائق."""
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(0, end - start)

    @property
    def attendance_rate(self) -> float:
        """نسبة الحضور في الحصة."""
        total = self.present_count + self.absent_count
        if total == 0:
            return 0.0
        return round((self.present_count / total) * 100, 2)


# =====================================================================
#  الحضور
# =====================================================================
class Attendance(TenantMixin, BaseModel):
    """
    سجل حضور رياضي في حصة تدريبية.

    يتم تسجيله عبر مسح رمز QR للبطاقة أو يدوياً من تطبيق المدرب.
    """

    __tablename__ = "attendance"

    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )

    session_date: Mapped[dt.date] = mapped_column(
        Date, nullable=False, index=True, comment="نسخة من تاريخ الحصة للبحث السريع"
    )

    status: Mapped[AttendanceStatus] = mapped_column(
        default=AttendanceStatus.PRESENT, nullable=False, index=True
    )
    method: Mapped[AttendanceMethod] = mapped_column(
        default=AttendanceMethod.MANUAL, nullable=False
    )

    minutes_late: Mapped[int] = mapped_column(
        Integer, default=0, comment="دقائق التأخر"
    )
    notes: Mapped[str | None] = mapped_column(Text, comment="ملاحظة أو سبب الغياب")
    excuse_document: Mapped[str | None] = mapped_column(
        String(255), comment="مرفق عذر"
    )

    recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    athlete: Mapped["Athlete"] = relationship(
        "Athlete", back_populates="attendance_records", lazy="selectin"
    )
    session: Mapped["TrainingSession"] = relationship(
        "TrainingSession", back_populates="attendance"
    )
    group: Mapped["Group | None"] = relationship("Group")
    recorded_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[recorded_by_id]
    )

    __table_args__ = (
        # قيد التفرد: لا يتكرر حضور نفس الرياضي في نفس الحصة
        UniqueConstraint("session_id", "athlete_id", name="uq_attendance_session_athlete"),
        Index("ix_attendance_club_date", "club_id", "session_date"),
        Index("ix_attendance_athlete_date", "athlete_id", "session_date"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"حضور {self.athlete_id} - {self.session_date}: {self.status}"

    @property
    def status_label(self) -> str:
        return {
            "present": "حاضر",
            "absent": "غائب",
            "late": "متأخر",
            "excused": "بعذر",
        }.get(self.status, self.status)

    @property
    def is_present(self) -> bool:
        return self.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


# =====================================================================
#  التقييمات الفنية
# =====================================================================
class Evaluation(TenantMixin, BaseModel):
    """
    تقييم دوري أو فني أو بدني من المدرب.

    المقاييس من 1 إلى 10 لكل محور، والنظام يحسب المتوسط العام.
    """

    __tablename__ = "evaluations"

    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )
    coach_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_members.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="SET NULL")
    )

    eval_date: Mapped[dt.date] = mapped_column(
        Date, nullable=False, index=True, comment="تاريخ التقييم"
    )
    type: Mapped[EvaluationType] = mapped_column(
        default=EvaluationType.PERIODIC, nullable=False, index=True
    )

    #: المحاور (كلها من 1 إلى 10)
    physical_score: Mapped[int | None] = mapped_column(
        Integer, comment="اللياقة البدنية"
    )
    technique_score: Mapped[int | None] = mapped_column(
        Integer, comment="المهارات الفنية"
    )
    discipline_score: Mapped[int | None] = mapped_column(
        Integer, comment="الانضباط"
    )
    teamwork_score: Mapped[int | None] = mapped_column(
        Integer, comment="الروح الجماعية"
    )

    #: المتوسط المحسوب تلقائياً
    overall_score: Mapped[float | None] = mapped_column(
        Numeric(4, 2), comment="المعدل العام (محسوب تلقائياً)"
    )

    strengths: Mapped[str | None] = mapped_column(Text, comment="نقاط القوة")
    weaknesses: Mapped[str | None] = mapped_column(Text, comment="نقاط التحسين")
    notes: Mapped[str | None] = mapped_column(Text, comment="ملاحظات عامة")
    recommendations: Mapped[str | None] = mapped_column(
        Text, comment="توصيات للولي"
    )

    #: هل اطّلع الولي على التقييم؟
    parent_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    athlete: Mapped["Athlete"] = relationship(
        "Athlete", back_populates="evaluations", lazy="selectin"
    )
    group: Mapped["Group | None"] = relationship("Group")
    coach: Mapped["StaffMember | None"] = relationship("StaffMember")

    __table_args__ = (
        Index("ix_evaluations_athlete_date", "athlete_id", "eval_date"),
        Index("ix_evaluations_club_date", "club_id", "eval_date"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"تقييم {self.athlete_id} بتاريخ {self.eval_date}"

    # -----------------------------------------------------------------
    @property
    def score_components(self) -> list[tuple[str, int | None]]:
        """قائمة أزواج (اسم المحور، الدرجة)."""
        return [
            ("اللياقة البدنية", self.physical_score),
            ("المهارات الفنية", self.technique_score),
            ("الانضباط", self.discipline_score),
            ("الروح الجماعية", self.teamwork_score),
        ]

    @property
    def average_score(self) -> float | None:
        """المتوسط العام (يُحسب من المحاور المتاحة فقط)."""
        values = [v for _, v in self.score_components if v is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    @property
    def grade(self) -> str:
        """التقديرLetter المقابل للمعدل."""
        avg = self.average_score
        if avg is None:
            return "-"
        if avg >= 9:
            return "ممتاز"
        if avg >= 7.5:
            return "جيد جداً"
        if avg >= 6:
            return "جيد"
        if avg >= 4.5:
            return "مقبول"
        return "ضعيف"