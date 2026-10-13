---
# النشر المجاني على Wispbyte
# ========================
#
# هذا الدليل يغطي الخطة المجانية بالكامل: التكلفة صفر، والرابط
# العام، والقيود التي لا يمكن تجاوزها في هذه الخطة.

## 1) ما الذي تعطيه الخطة المجانية

| المورد | القيمة |
|---|---|
| عدد الخوادم | **1** (الخانة الوحيدة) |
| الذاكرة | 512 ميغابايت |
| القرص | 1 غيغابايت |
| المعالج | 35% من نواة واحدة |
| قاعدة بيانات مدارة | ❌ Premium فقط |
| نطاق فرعي مخصّص | ❌ Premium فقط |
| شرط التشغيل | تسجيل دخول كل أسبوعين |

قاعدة البيانات والنطاق الفرعي both مقفلان خلف Premium (نصّ اللوحة:
*"Managed MySQL ... available on premium servers"*، و*"Custom domain and
free subdomain"*). لذلك الخطة المجانية = **خانة واحدة + SQLite**.

## 2) الرابط المجاني — sslip.io

بلا حساب، بلا تسجيل، بلا انتظار: `sslip.io` يحوّل أي عنوان IP إلى اسم
مضيف. جرّبته فحُوِّل فوراً:

```bash
nslookup nadina.78.154.103.50.sslip.io
# Name:   nadina.78.154.103.50.sslip.io
# Address: 78.154.103.50
```

### الرابط النهائي

```
http://nadina.78.154.103.50.sslip.io:11827
```

### لماذا يبقى المنفذ في الرابط

لأن الخادم يقدّم HTTP على `IP:PORT` مباشرة. لا يوجد أمامه وكيل عكسي
يُخفي المنفذ، ولاTLS — فهو إضافة على نفس المضيف. المنفذ لا يظهر إلا
في الرابط النصي، وفي كل صفحات التطبيق.

### لماذا يعمل مع التطبيق

المنصة لا تفرض قيوداً على المضيف إلا إذا ضُبط `ALLOWED_HOSTS`:

```python
if not settings.DEBUG and settings.ALLOWED_HOSTS.strip():
    app.add_middleware(TrustedHostMiddleware, ...)
```

في `wispbyte.env` لا يوجد `ALLOWED_HOSTS`، فالحارس معطّل ويقبل أي
مضيف — بما فيه نطاق sslip.io. تحقّقت من ذلك عملياً بإرسال ترويسة
`Host: nadina.78.154.103.50.sslip.io` فجاءت كل المسارات 200.

## 3) الأرقام الثابتة

| القيمة | من أين |
|---|---|
| `78.154.103.50` | Console → Server info → Address |
| `11827` | Console → Server info → Address (بعد النقطتين) |
| `aa25a161` | Support tag |

عند تغيّر المنفذ (إعادة إنشاء الخادم) يتغيّر الرابط. حدّث عندها
`DEFAULT_PORT` عبر:

```bash
python scripts/build_wispbyte.py --port <NEW_PORT>
```

## 4) خطوات النشر

```bash
# 1) ابنِ الحزمة (تشمل قاعدة بيانات مبذورة بالمزامنة)
python scripts/build_wispbyte.py --port 11827 --cookie-secure false

# 2) ارفع wispbyte_deploy.zip إلى Files → Upload Files
# 3) كليك يمين على الملف → Unarchive
# 4) Startup → تأكد أن الأمر هو:  python main.py
# 5) Console → Start (يتطلّب إكمال التحقق البشري)
```

## 5) عن HTTPS والكوكي

الخادم يقدّم HTTP فقط، فالكوكي لا يمكن أن يحمل `Secure` — وإلا لن
يُرسَل أصلاً ولن يعمل الدخول. لذلك تُبنى الحزمة مع:

```
COOKIE_SECURE=false
ALLOW_INSECURE_COOKIES=true
```

`ALLOW_INSECURE_COOKIES` هو **إقرار صريح** لا قيمة افتراضية: الإنتاج
يرفض `COOKIE_SECURE=false` ما لم تضبطه صراحةً. فائدة ذلك أن الخيار
غير الآمن يكون مرئياً في الإعدادات بدل أن يُضعَف الحارس بصمت.

عند الانتقال إلى Premium، اضبط الأمرين `true` معاً.

## 6) إن أردت نطاقاً أنظف لاحقاً

| الخيار | التكلفة | النتيجة |
|---|---|---|
| Premium في Wispbyte | €0.33/شهر | `nadina` على نطاقاتهم |
| DuckDNS | مجاني (5 نطاقات) | `nadina.duckdns.org` (يبقى المنفذ) |
| شراء `.dz` | ~$40/سنة | `nadina.dz` + سجل A على `78.154.103.50` |
| Render.com | مجاني | `nadina.onrender.com` HTTPS بلا منفذ |

Render يستحق التوقف: يمنح HTTPS ونطاقاً بلا منفذ. لكنه يحتاج
مستودع GitHub ودفع الكود — راجع `DEPLOY.md`.
