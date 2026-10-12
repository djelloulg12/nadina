/* =============================================================================
 * المنصة الشاملة — Service Worker (PWA)
 * -----------------------------------------------------------------------------
 * استراتيجية التخزين المؤقت:
 *   • ملفات /static/  →  Cache First (الأداء الأقصى، الملفات ثابتة بإصدارات)
 *   • طلبات التنقل    →  Network First مع الرجوع إلى الذاكرة المؤقتة
 *   • /api/          →  لا تُخزَّن أبداً (بيانات حيّة)
 * ========================================================================== */

'use strict';

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'mashia-' + CACHE_VERSION;

/** الهيكل الأساسي الذي نخزّنه مسبقاً عند التثبيت. */
const PRECACHE_URLS = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/icons/icon.svg',
  '/'
];

const STATIC_PATH = '/static/';
const API_PATH = '/api/';

/* -----------------------------------------------------------------------------
 * التثبيت: تخزين مسبق للهيكل ثم التفعيل الفوري
 * -------------------------------------------------------------------------- */
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      // نخزّن كل عنصر على حدة حتى لا يُفشل عنصرٌ واحد التثبيت كلّه
      await Promise.all(
        PRECACHE_URLS.map(async (url) => {
          try {
            const request = new Request(url, { cache: 'reload', credentials: 'same-origin' });
            const response = await fetch(request);
            if (response && response.ok) {
              await cache.put(url, response.clone());
            }
          } catch (err) {
            // عنصر غير متاح الآن — نتخطّاه ونكمل
          }
        })
      );
      await self.skipWaiting();
    })()
  );
});

/* -----------------------------------------------------------------------------
 * التفعيل: حذف الذاكرات القديمة والاستحواذ على العملاء فوراً
 * -------------------------------------------------------------------------- */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      if (self.registration.navigationPreload) {
        try {
          await self.registration.navigationPreload.enable();
        } catch (err) {
          /* غير مدعوم */
        }
      }

      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith('mashia-') && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );

      await self.clients.claim();
    })()
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

/* -----------------------------------------------------------------------------
 * الاعتراض: توجيه كل طلب حسب نوعه
 * -------------------------------------------------------------------------- */
self.addEventListener('fetch', (event) => {
  const { request } = event;

  // نتجاهل كل ما ليس GET
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }

  // نتجاهل النطاقات الخارجية
  if (url.origin !== self.location.origin) return;

  // لا تخزين مطلقاً لواجهات الـ API
  if (url.pathname.startsWith(API_PATH)) return;

  // طلبات التنقل (فتح الصفحات)
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigation(event));
    return;
  }

  // الملفات الثابتة
  if (url.pathname.startsWith(STATIC_PATH)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // باقي الموارد (صور، خطوط، أيقونات…): الشبكة أولاً ثم الذاكرة المؤقتة
  event.respondWith(networkFirst(request));
});

/* -----------------------------------------------------------------------------
 * الدوال المساعدة
 * -------------------------------------------------------------------------- */

/** الشبكة أولاً مع الرجوع إلى الذاكرة المؤقتة ثم إلى الصفحة الرئيسية. */
async function handleNavigation(event) {
  const request = event.request;
  try {
    const preload = await event.preloadResponse;
    if (preload) {
      putInCache(request, preload.clone());
      return preload;
    }

    const response = await fetch(request);
    putInCache(request, response.clone());
    return response;
  } catch (err) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;

    const shell = await caches.match('/');
    if (shell) return shell;

    return new Response(
      '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<title>المنصة الشاملة — دون اتصال</title>' +
      '<style>body{font-family:"Cairo","Tajawal","Segoe UI",system-ui,sans-serif;' +
      'background:#f8fafc;color:#0f172a;display:flex;align-items:center;justify-content:center;' +
      'height:100vh;margin:0;text-align:center;padding:2rem}' +
      '.box{max-width:420px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;' +
      'padding:32px;box-shadow:0 8px 24px rgba(15,23,42,.08)}' +
      'h1{color:#0f766e;font-size:20px;margin:0 0 8px}' +
      'p{color:#64748b;font-size:14px;line-height:1.7;margin:0}</style></head><body>' +
      '<div class="box"><h1>لا يوجد اتصال بالإنترنت</h1>' +
      '<p>تعذّر تحميل الصفحة لأن الاتصال غير متاح. أعد المحاولة عند استعادة الشبكة.</p></div>' +
      '</body></html>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } }
    );
  }
}

/** الذاكرة المؤقتة أولاً (للملفات الثابتة غير المتغيّرة). */
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === 'basic') {
      putInCache(request, response.clone());
    }
    return response;
  } catch (err) {
    const fallback = await caches.match(request, { ignoreSearch: true });
    if (fallback) return fallback;
    throw err;
  }
}

/** الشبكة أولاً مع الرجوع إلى الذاكرة المؤقتة عند انقطاع الشبكة. */
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === 'basic') {
      putInCache(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw err;
  }
}

/** يحفظ استجابة في الذاكرة المؤقتة دون أن يوقف الاستجابة الأصلية. */
function putInCache(request, response) {
  if (!response || !response.ok || response.type === 'opaque') return;
  const copy = response.clone();
  caches
    .open(CACHE_NAME)
    .then((cache) => cache.put(request, copy))
    .catch(() => {
      /* تجاهل أخطاء التخزين (امتلاء المساحة مثلاً) */
    });
}
