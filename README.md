# المنصة الشاملة — إدارة الأندية الرياضية
### Multi-Tenant Sports Clubs Management SaaS

<div dir="rtl">

منصة سحابية متعددة الأندية لتسيير الرياضيين والأفواج والحصص والحضور
والتقييمات والتسيير المالي — بواجهة عربية كاملة (RTL) و REST API.

</div>

---

## المحتويات

1. [نظرة سريعة](#نظرة-سريعة)
2. [التشغيل](#التشغيل)
3. [البنية المعمارية](#البنية-المعمارية)
4. [عزل المستأجرين](#عزل-المستأجرين-نواة-الأمان)
5. [نظام الصلاحيات](#نظام-الصلاحيات)
6. [بنية قاعدة البيانات](#بنية-قاعدة-البيانات)
7. [الواجهة البرمجية](#الواجهة-البرمجية)
8. [الترحيلات](#الترحيلات)
9. [الاختبارات](#الاختبارات)
10. [النشر للإنتاج](#النشر-للإنتاج) — الدليل الكامل في [`DEPLOY.md`](DEPLOY.md)

---

## نظرة سريعة

| | |
|:--|:--|
| **الإطار** | FastAPI 0.141 · Python 3.12+ |
| **قاعدة البيانات** | SQLAlchemy 2.0 (async) · SQLite للتطوير · PostgreSQL للإنتاج |
| **الواجهة** | Jinja2 (SSR) · HTML/CSS/JS بدون أدوات بناء · PWA |
| **المصادقة** | JWT (للـ API) · كوكي موقّع (للواجهة) · bcrypt |
| **الترحيلات** | Alembic |
| **الاختبارات** | pytest + httpx (96 اختباراً) |

---

## التشغيل

### المتطلبات

- Python 3.12 أو أحدث
- SQLite (مضمّن) أو PostgreSQL 14+

### الخطوات

```bash
# 1) تثبيت التبعيات
pip install -r requirements.txt

# 2) إعداد البيئة
cp .env.example .env
# ثم ولّد مفتاحاً سرياً:
python -c "import secrets; print(secrets.token_urlsafe(48))"
# وضع الناتج في SECRET_KEY داخل .env

# 3) إنشاء جداول قاعدة البيانات
python run.py migrate          # أو: alembic upgrade head

# 4) (اختياري) بيانات تجريبية واقعية
python run.py seed

# 5) تشغيل التطبيق
python run.py serve            # أو: python -m uvicorn app.main:app --reload
```

> كل الأوامر الاختصار لـ `run.py`، ويمكن تنفيذ نفس الخطوات مباشرة عبر
> `alembic` و `python -m scripts.seed` و `uvicorn`.

ثم افتح <http://localhost:8000>

### حسابات الدخول التجريبية

بعد `python -m scripts.seed` — كلمة المرور للجميع `Mashia@2024`:

| الرتبة | المعرّف | الوصف |
|:--|:--|:--|
| مدير المنصة | `admin@mashia.dz` | يرى كل الأندية |
| مدير نادي | `admin1@toufik-sport.dz` | نادي التوفيق |
| إداري / مدرب / ولي | `reception1@…` · `coach11@…` · `parent1@example.dz` | روابط النمط في مخرجات السكربت |

> ⚠️ **غيّر كلمة المرور فوراً** في أي بيئة حقيقية. البيانات التجريبية
> للعرض فقط.

---

## البنية المعمارية

```
المنصة الشاملة/
├── app/
│   ├── core/                    ← النواة: لا تعتمد على أي طبقة أخرى
│   │   ├── config.py            إعدادات مؤمَّنة (pydantic-settings)
│   │   ├── db.py                المحرك، الجلسات، utcnow() المرجع الوحيد
│   │   ├── schema.py            مزامنة المخطط مع ترحيلات Alembic
│   │   ├── tenancy.py           ★ عزل المستأجرين (4 طبقات دفاع)
│   │   ├── rbac.py              مصفوفة الصلاحيات
│   │   ├── security.py          bcrypt · JWT · جلسات موقّعة
│   │   ├── exceptions.py        استثناءات الأعمال برسائل عربية
│   │   └── errors.py            معالجات الأخطاء الموحّدة
│   │
│   ├── models/                  ← نماذج ORM (23 جدولاً)
│   │   ├── base.py              Base · Mixins · أنواع التعدادات
│   │   ├── enums.py             كل ثوابت النظام
│   │   ├── platform.py          User · Club · Sport · Plan
│   │   ├── people.py            Athlete · ParentLink · StaffMember
│   │   ├── training.py          Group · Session · Attendance · Evaluation
│   │   ├── finance.py           FeePlan · Invoice · Payment · Expense
│   │   └── content.py           News · Registration · Notification · AuditLog
│   │
│   ├── schemas/                 ← مخططات Pydantic (التحقق + توثيق API)
│   ├── services/                ← منطق الأعمال (بلا HTTP)
│   │   ├── auth.py  people.py  training.py  finance.py
│   │   ├── clubs.py  notifications.py  reports.py
│   │   └── excel.py  cards.py
│   │
│   ├── api/                     ← REST API (JSON)
│   │   ├── deps.py              المصادقة وبناء سياق المستأجر
│   │   └── v1/                  auth · public · club · coach · parent · admin
│   │
│   ├── web/                     ← صفحات HTML (نفس طبقة الخدمات)
│   │   ├── templates_setup.py   محرّك القوالب + الفلاتر العربية
│   │   ├── helpers.py           الجلسات وسياق القالب
│   │   └── routes*.py           المسارات
│   │
│   ├── static/                  ← CSS · JS · PWA (بدون أدوات بناء)
│   └── templates/               ← 47 قالب Jinja2
│
├── alembic/                     ← الترحيلات
├── scripts/                     ← seed · فحوص الصيانة
├── tests/                       ← 96 اختباراً
└── requirements.txt
```

### قاعدة التصميم: تدفّق الطلب

```
طلب HTTP
   │
   ├─► Middleware ................ قياس الأداء + تنظيف السياق
   │
   ├─► api/deps.get_db ......... جلسة قاعدة البيانات
   │
   ├─► api/deps.get_current_user
   │     ├─ قراءة الـ JWT أو كوكي الجلسة
   │     ├─ تحميل المستخدم
   │     └─★ establish_tenant_context() ← يكتب ContextVar
   │
   ├─► require_roles(...) ....... حارس الرتبة على مستوى الموجّه
   ├─► require_permission(...) .. فحص الصلاحية الدقيقة
   │
   ├─► طبقة الخدمات ............. منطق الأعمال
   │     └─ كل استعلام ORM يمرّ على مستمع العزل تلقائياً
   │
   └─► استجابة (JSON أو HTML)
```

**النقطة الجوهرية**: طبقة الخدمات لا تعرف شيئاً عن HTTP ولا عن مصدر
الهوية. ترى كائن `User` جاهزاً وسياق عزل مضبوطاً، سواء جاء الطلب من
متصفح أو تطبيق أو أداة اختبار.

---

## عزل المستأجرين (نواة الأمان)

كل جدول يملك نادياً يحمل `club_id`. أي تسريب في هذا الملف يعني تسريب
بيانات بين الأندية، لذلك بُني على **أربع طبقات دفاعية**:

### الطبقة 1 — حقن شرط التصفية في كل استعلام

```python
# app/core/tenancy.py
@event.listens_for(Session, "do_orm_execute")
def _enforce_tenant_scope(state):
    ctx = get_tenant_context()          # من ContextVar
    if ctx is None or ctx.bypasses_isolation:
        return
    for mapper in Base.registry.mappers:
        if not _is_tenant_owned(mapper.class_):
            continue
        criterion = _resolve_scope_criterion(mapper.class_, ctx)
        session.with_loader_criteria(
            mapper.class_, criterion,
            include_aliases=True,
            propagate_to_loaders=True,
        )
```

**لماذا المستمع وليس استدعاء عند إنشاء الجلسة؟**
لأن ترتيب تهيئة الاعتماديات في FastAPI غير مضمون — قد تُنشأ الجلسة قبل أن
يُعرف من هو المستخدم. المستمع يقرأ السياق *لحظة تنفيذ الاستعلام*، فيغطي
`session.get()` والتحميل الكسول والعلاقات، ولا يمكن لكود مستقبلي أن
«ينسى» العزل.

`propagate_to_loaders=True` هو ما يمنع التسريب عبر العلاقات:
`invoice.athlete` تُحمَّل بنفس شرط الفلترة.

### الطبقة 2 — حارس ما قبل الكتابة

مستمع `before_flush` يمنع:

- إنشاء سجل لـ `club_id` مختلف
- نقل سجل قائم إلى نادي آخر
- تفريغ `club_id` من سجل قائم

### الطبقة 3 — `ContextVar` معزول لكل طلب

`ContextVar` آمن مع `asyncio`: كل طلب يعمل في سياق خاص به، فلا تتسرّب
حالة طلب إلى آخر. الوسيط `tenant_context_middleware` يضمن نقطة بداية
نظيفة لكل طلب.

### الطبقة 4 — تحقق صريح في طبقة الخدمات

عمليات تمس جداول متعددة (ربط رياضي بفوج، تسجيل دفعة) تستدعي
`assert_same_club()` قبل التنفيذ لضمان تكامل المراجع.

### أنماط النطاق الثلاثة

| النطاق | الشرط المطبَّق | من |
|:--|:--|:--|
| **عام** | لا يُحقن أي شرط | مدير المنصة |
| **نادي** | `club_id = :current` | طاقم النادي |
| **أولياء** | `athlete_id IN (أبنائي)` | الولي الموحّد |

نطاق الولي **يعبر أندية متعددة عمداً** — هذا هو المقصود من «حساب الولي
الموحد»: يرى ابنين في ناديين مختلفين دون أن يرى رياضي غيره في نفس النادي.
يُعرَّف عبر `__parent_scope__ = "<العمود>:<النطاق>"` على كل نموذج.

### الإثبات

```bash
pytest tests/test_tenancy.py tests/test_integration.py -v
```

12 اختبار عزل + اختبارات تكامل تثبت: أن مدير النادي «أ» لا يرى رياضي
«ب» عبر `session.get()` أو الاستعلام المركّب أو العلاقة الكسولة، وأن
بطاقة من نادي آخر تُرفض برسالة 404 (لا 403) حتى لا نكشف وجودها.

---

## نظام الصلاحيات

مصفوفة صريحة في `app/core/rbac.py` — رموز نصية بصيغة
`<المورد>.<العملية>`:

```python
has_permission(user.role, "athletes.create")   # → True/FALSE
```

| الرتبة | النطاق | أبرز الصلاحيات |
|:--|:--|:--|
| مدير المنصة | كل الأندية | كل شيء (`*`) |
| مدير النادي | ناديه | كل شيء داخل النادي |
| إداري / سكرتير | ناديه | إضافة رياضيين · دفعات · بطاقات (**بلا حذف**) |
| مدرب | أفواجه فقط | حضور · تقييمات (**بلا مالية**) |
| ولي أمر | أبناؤه فقط | قراءة (حضور · تقييم · مالية أبنائه) |
| رياضي | نفسه | قراءة فقط |

**طبقتان من الحماية** تُطبَّقان معاً:

1. حارس الرتبة على مستوى الموجّه — يمنع الرتب غير المناسبة من الوصول
   لمسارات إدارة النادي أصلاً.
2. الصلاحية الدقيقة لكل مسار.

طبقة الصلاحيات وحدها ليست كافية (ولي الأمر يملك `athletes.read` لكن
مسار `/club/athletes` ليس مكانه)، وطبقة الرتب وحدها ليست دقيقة بما يكفي.

---

## بنية قاعدة البيانات

23 جدولاً في ست مجموعات:

| المجموعة | الجداول |
|:--|:--|
| **المنصة** | `clubs` · `users` · `sports` · `club_sports` · `platform_plans` · `club_subscriptions` |
| **الأشخاص** | `athletes` · `parent_links` · `staff_members` |
| **التدريب** | `groups` · `group_athletes` · `training_sessions` · `attendance` · `evaluations` |
| **المالية** | `fee_plans` · `invoices` · `payments` · `expenses` |
| **المحتوى** | `club_news` · `registration_requests` · `notifications` |
| **النظام** | `audit_logs` · `daily_digests` |

### مبادئ تصميم متّفق عليها

- **الفاتورة هي المرجع** — حالة الفاتورة تُشتق دائماً من مجموع دفعاتها،
  لا تُضبط يدوياً أبداً.
- **الحذف المنطقي** افتراضياً (`is_active`) — الفواتير المدفوعة لا
  تُحذف أبداً.
- **حذف مدرب مرتبط بأفواج** مرفوض (قاعدة عمل صريحة).
- **حذف رياضي عليه ديون** مرفوض (قاعدة محاسبية).
- **أعمدة التعدادات** تُخزَّن `VARCHAR` مع قيد `CHECK` — لا `ENUM` أصلي،
 لضمان قابلية النقل الكاملة بين SQLite و PostgreSQL.

---

## الواجهة البرمجية

- **الوثائق التفاعلية**: <http://localhost:8000/api/docs>
- **مخطط OpenAPI**: `/api/openapi.json`

### أمثلة

```bash
# البوابة العامة — لا تحتاج مصادقة
curl "http://localhost:8000/api/v1/public/clubs?sport_id=1"

# تسجيل الدخول
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"identifier":"admin1@nadi-altoufiq-riadi.dz","password":"Mashia@2024"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# قائمة الرياضيين (مُقيّدة بالنادي تلقائياً)
curl localhost:8000/api/v1/club/athletes -H "Authorization: Bearer $TOKEN"

# تسجيل حضور بمسح بطاقة QR
curl -X POST localhost:8000/api/v1/club/sessions/1/attendance/scan \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"card_code":"AA1-0001"}'

# تصدير الرياضيين إلى Excel
curl localhost:8000/api/v1/club/export/athletes -H "Authorization: Bearer $TOKEN" -o athletes.xlsx
```

### مجموعات المسارات

| البادئة | المحتوى | الحماية |
|:--|:--|:--|
| `/api/v1/auth` | الدخول، الرموز، الملف الشخصي | مفتوح / مصادَق |
| `/api/v1/public` | دليل الأندية، الأخبار، التسجيل | مفتوح |
| `/api/v1/club` | إدارة النادي | طاقم النادي |
| `/api/v1/coach` | مساحة المدرب | المدرب |
| `/api/v1/parent` | بوابة الولي | الولي |
| `/api/v1/admin` | إحصاءات المنصة | مدير المنصة |

---

## الترحيلات

```bash
alembic upgrade head                      # تطبيق
alembic downgrade -1                      # تراجع خطوة
alembic revision --autogenerate -m "..."   # توليد من النماذج
alembic check                             # كشف انحراف المخطط
```

رابط قاعدة البيانات يُقرأ من `.env` عبر `settings.sync_database_url` —
فلا تنتج بيئة تطوير عناوين اتصال لا تطابق الإنتاج.

### مصدر واحد للحقيقة

المخطط يُنشأ من ملفات الترحيل **لا** من `create_all` — في
`scripts/seed.py` وكل ما يهيّئ قاعدة بيانات. السبب عملي لا نظري:

`create_all` ينتج قاعدة بيانات لا يعرفها Alembic، فيحكم `alembic check`
عليها بأنها «ليست محدَّثة» ويقترح ترحيلاً فارغاً يخدع المطور
ويُعيد كتابة المخطط بلا فائدة. مع الترحيلات، المخطط المُنشأ
والـ migration المرجعي متطابقان دائماً.

`app/core/schema.py` يوفّر `sync_schema(reset=...)` التي يستدعيها
السكربت، وتُبقي محرّك التطبيق مغلقاً أثناء عمل Alembic في عملية منفصلة
(إلزامي مع SQLite).

---

## الاختبارات

```bash
pytest                       # كل الاختبارات (96)
pytest tests/test_tenancy.py # عزل المستأجرين فقط (12)
pytest --tb=short -q         # مخرجات موجزة
```

| الملف | العدد | ما يغطّيه |
|:--|--:|:--|
| `test_tenancy.py` | 12 | الطبقات الأربع للعزل · نطاق الولي · حارس الكتابة |
| `test_integration.py` | 33 | المصادقة · الصلاحيات · العزل عبر الـ API · المالية · الاستيراد |
| `test_web_pages.py` | 51 | كل صفحة HTML · الحماية · التنقل حسب الرتبة · الأصول |

### الفحوص الآلية

```bash
python run.py check     # كل الفحوص دفعة واحدة
```

| الفحص | يحوّل خطأً… |
|:--|:--|
| `check_encoding.py` | تلف ترميز (حروف CJK متسرّبة) إلى فشل آلي |
| `check_readme.py` | رابط جدول محتويات مكسور إلى فشل آلي |
| `check_templates.py` | خطأ نحوي في قالب إلى فشل آلي قبل النشر |
| `check_css.py` | صنف CSS غير معرَّف (خطأ بصري لا يظهر إلا بالنظر) |
| `check_schema_match.py` | نموذج يخالف ملف الترحيل (انحراف صامت) |
| `alembic check` | تغيير في النماذج بلا ترحيل مقابل |
| `pytest` | سلوك خاطئ في المصادقة أو العزل أو العرض |

### أوامر أخرى

```bash
python run.py serve        # خادم التطوير
python run.py seed         # بيانات تجريبية
python run.py reset        # حذف كل شيء + تعبئة
python run.py migrate      # تطبيق الترحيلات
python run.py routes       # عرض كل المسارات (110 API + 77 صفحة)
python run.py verify       # فحص حيّ على خادم مُشغَّل
```

`run.py verify` يفتح الاتصال بخادم يعمل ويختبر المسارات العامة،
وحماية الصفحات، وتدفّق الدخول، ولوحة النادي، وتصدير Excel — أي خطأ
في طبقة HTTP لا يلحقه pytest.

---

## النشر للإنتاج

**الدليل الكامل في [`DEPLOY.md`](DEPLOY.md)** — نشر على Render خطوة بخطوة.

### نظرة سريعة

```bash
# 1) ارفع الكود
git init && git add . && git commit -m "المنصة الشاملة"

# 2) أنشئ مستودعاً على GitHub ثم
git remote add origin https://github.com/<اسمك>/mashia-platform.git
git push -u origin main

# 3) في Render: New → Blueprint → اختر المستودع
#    Render يقرأ render.yaml وينشئ الخدمة + PostgreSQL

# 4) تحقّق بعد النشر
python scripts/verify.py https://<your-app>.onrender.com
```

### ما الذي يهيّئه `render.yaml`

| المورد | التفاصيل |
|:--|:--|
| قاعدة بيانات | PostgreSQL، خطة مجانية (1 غيغابايت) |
| خدمة الويب | Docker، خطة مجانية، منطقة فرانكفورت |
| `SECRET_KEY` | يُولَّد تلقائياً — لا يُكتب في الملف |
| الترحيلات | تُطبَّق تلقائياً عند كل نشر |

### الملفات المسؤولة

| الملف | الدور |
|:--|:--|
| `Dockerfile` | صورة إنتاج ببناء على مرحلتين (ت.build ثم تشغيل) |
| `.dockerignore` | يمنع رفع `.env` وقواعد البيانات المحلية |
| `render.yaml` | Blueprint: الخدمة + القاعدة + متغيرات البيئة |
| `scripts/startup.py` | يطبّق الترحيلات ثم يشغّل الخادم |
| `.gitignore` | يستثني الأسرار وقواعد البيانات |

### قائمة التحقق قبل النشر

- [ ] `SECRET_KEY` عشوائي وطويل (48+ حرفاً)
- [ ] `DEBUG=false`
- [ ] `COOKIE_SECURE=true` (إجباري — تفرضه الإعدادات)
- [ ] `DATABASE_URL` يشير إلى PostgreSQL حقيقي
- [ ] `ALLOWED_HOSTS` يطابق نطاقك
- [ ] الترحيلات طُبِّقت (تظهر في Logs)
- [ ] `SEED_ON_EMPTY=false` إن كانت بياناتك حقيقية

### ملاحظات تقنية

**سائق قاعدة البيانات.** مزوّدو الاستضافة يعطون الرابط بصيغة
`postgresql://` بلا سائق. SQLAlchemy يختار عندها `psycopg2` المتزامن
فينهار التطبيق عند أول استعلام. `app/core/config.py::_with_async_driver`
يضيف `asyncpg` تلقائياً، و`tests/test_production_config.py` يحمي ذلك.

**الوكيل العكسي.** Render يستقبل HTTP ويتصل بالتطبيق عبر HTTP بينما
المتصفح يستخدم HTTPS. بدون `ProxyHeadersMiddleware` يبني التطبيق روابط
`http://`، وتُرفض الكوكيز ذات `secure=True`. الوسيط موثّق عبر
`TRUSTED_PROXIES` لا عبر `*` — حتى لا يثق التطبيق بترويسات يرسلها
المستخدم مباشرة.

**إيقاف الخمول.** الخطة المجانية توقّف الحاوية بعد 15 دقيقة خمول.
أول طلب بعدها ينتظر ~50 ثانية. خدمة ping دورية (UptimeRobot مجاني)
تمنع ذلك.

**قاعدة البيانات المجانية.** تنتهي قاعدة Render بعد 30 يوماً. البديل
الدائم المجاني: Neon — انظر `DEPLOY.md`.

**بقية الضوابط** (من الإعدادات نفسها):

- الإعدادات **ترفض** `DEBUG=true` في الإنتاج وتُجبر `COOKIE_SECURE=true`.
- المعالج الأخير للأخطاء لا يسرّب تفاصيل تقنية في الإنتاج.
- **البيانات الطبية** (`athletes.medical_notes`) لا تظهر في أي استجابة
  للولي — الوصول إليها مقصور على من يملك صلاحية.

---

## رخصة

MIT
