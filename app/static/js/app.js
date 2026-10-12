/**
 * ============================================================================
 * المنصة الشاملة — سكربت الواجهة الأمامية (app.js)
 * منصة multi-tenant لإدارة الأندية الرياضية — واجهة عربية RTL
 * ----------------------------------------------------------------------------
 * وحدة: JavaScript خالص (Vanilla ES6+) بدون أي مكتبات خارجية أو أدوات بناء.
 * كل وحدة محمية بـ try/catch حتى لا يعطّل عطلٌ في-widget الصفحةَ بالكامل.
 *
 * الوحدات المتاحة على الكائن العام window.MashiaApp:
 *   Theme · Sidebar · Modals · Toasts · Forms · Confirm · Attendance
 *   Notifications · Filters · Charts · Print · Tables · PWA · Money · Dropdowns · Tabs
 *
 * أمثلة:
 *   MashiaApp.Toasts.show('تم الحفظ بنجاح', 'success');
 *   MashiaApp.Confirm.request({ title: 'تأكيد' }).then(ok => { ... });
 *   MashiaApp.Attendance.scan({ sessionId: 12, onDetected: code => ... });
 *   MashiaApp.Charts.bar(canvasEl, ['يناير','فبراير'], [12, 18]);
 * ============================================================================
 */
(function (window, document) {
  'use strict';

  /* ==========================================================================
   * أدوات مساعدة عامة (Helpers)
   * ========================================================================== */
  var MashiaApp = window.MashiaApp || {};
  window.MashiaApp = MashiaApp;

  MashiaApp.version = '1.0.0';

  MashiaApp.config = {
    locale: 'ar-DZ',          /* locales ar-DZ تستخدم الأرقام اللاتينية */
    currency: 'DZD',
    currencySymbol: 'د.ج',
    apiBase: '/api/v1',
    toastTimeout: 5000,
    notificationsInterval: 60000,   /* 60 ثانية */
    filtersDebounce: 400,
    attendanceEndpoint: '/api/v1/sessions/{id}/attendance/scan'
  };

  var doc = document;

  function qs(selector, ctx) {
    try { return (ctx || doc).querySelector(selector); } catch (e) { return null; }
  }

  function qsa(selector, ctx) {
    try { return Array.prototype.slice.call((ctx || doc).querySelectorAll(selector)); }
    catch (e) { return []; }
  }

  /** تنفيذ الدالة عند جاهزية المستند (مرة واحدة فقط). */
  function onReady(fn, ctx) {
    if (typeof fn !== 'function') return;
    if (doc.readyState === 'loading' || doc.readyState === 'uninitialized') {
      doc.addEventListener('DOMContentLoaded', function () { fn.call(ctx); }, { once: true });
    } else {
      try { fn.call(ctx); } catch (err) { debug('onReady', err); }
    }
  }

  /** يشغّل الدالة ويبتلع أي خطأ (لا يكسر الصفحة أبداً). */
  function guard(name, fn) {
    try {
      return fn.apply(null, Array.prototype.slice.call(arguments, 2));
    } catch (err) {
      debug(name, err);
      return undefined;
    }
  }

  function debug(name, err) {
    var c = window.console;
    if (!c) return;
    if (typeof c.debug === 'function') c.debug('[MashiaApp] ' + name + ':', err);
    else if (typeof c.log === 'function') c.log('[MashiaApp] ' + name + ':', err);
  }

  /* ---------------------------- التخزين المحلي ---------------------------- */
  function safeStorage() {
    try {
      var s = window.localStorage;
      var probe = '__mashia_probe__';
      s.setItem(probe, '1');
      s.removeItem(probe);
      return s;
    } catch (e) { return null; }
  }

  function getStored(key, fallback) {
    var s = safeStorage();
    if (!s) return fallback;
    try {
      var v = s.getItem(key);
      return (v === null || v === undefined || v === '') ? fallback : v;
    } catch (e) { return fallback; }
  }

  function setStored(key, value) {
    var s = safeStorage();
    if (!s) return false;
    try { s.setItem(key, String(value)); return true; } catch (e) { return false; }
  }

  function removeStored(key) {
    var s = safeStorage();
    if (!s) return false;
    try { s.removeItem(key); return true; } catch (e) { return false; }
  }

  /* ------------------------------ مساعدات عامة ----------------------------- */
  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var ctx = this, args = arguments;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        timer = null;
        guard('debounce', function () { fn.apply(ctx, args); });
      }, wait || 200);
    };
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isRTL() {
    var dir = (doc.documentElement.getAttribute('dir') || '').toLowerCase();
    return dir === 'rtl';
  }

  /** يهرّب مُعرّفاً لاستخدامه في مُحدِّد CSS (مع بديل للمتصفحات القديمة). */
  function cssEscape(value) {
    var str = String(value === null || value === undefined ? '' : value);
    if (window.CSS && typeof window.CSS.escape === 'function') {
      try { return window.CSS.escape(str); } catch (e) { /* fallback */ }
    }
    return str.replace(/[^a-zA-Z0-9_\-]/g, function (ch) { return '\\' + ch; });
  }

  /** يحوّل مُعرّفاً نصياً أو عنصراً إلى عنصر DOM (يقبل "#id" أو "id"). */
  function resolveElement(target) {
    if (!target) return null;
    if (typeof target === 'string') {
      var sel = target.charAt(0) === '#' ? target : '#' + target;
      return qs(sel) || qs(target);
    }
    if (target.nodeType === 1) return target;
    if (typeof target.length === 'number' && target[0] && target[0].nodeType === 1) return target[0];
    return null;
  }

  /** تحويل الأرقام العربية/الهندية إلى أرقام لاتينية + تنظيف الفواصل. */
  function normalizeNumber(value) {
    if (typeof value === 'number') return value;
    if (value === null || value === undefined) return NaN;
    var s = String(value).trim();
    if (!s) return NaN;
    var arabic = '٠١٢٣٤٥٦٧٨٩';
    var indic = '۰۱۲۳۴۵۶۷۸۹';
    var out = '';
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      var idx = arabic.indexOf(ch);
      if (idx === -1) idx = indic.indexOf(ch);
      out += (idx !== -1) ? String(idx) : ch;
    }
    out = out.replace(/[\u066C\u00A0\u202F\u2009]/g, '') /* فواصل الآلاف */;
    if (out.indexOf('٫') !== -1) out = out.replace(/٫/g, '.');
    else out = out.replace(/,/g, '');
    out = out.replace(/\s/g, '');
    if (out === '' || out === '-' || out === '.') return NaN;
    return parseFloat(out);
  }

  function toNumber(value, fallback) {
    var n = normalizeNumber(value);
    return isNaN(n) ? (fallback === undefined ? 0 : fallback) : n;
  }

  function csrfToken() {
    var meta = qs('meta[name="csrf-token"]');
    return meta ? (meta.getAttribute('content') || '') : '';
  }

  /** طبقة اتصال موحّدة مع الـ API — لا ترمي استثناءات أبداً. */
  MashiaApp.api = {
    url: function (path) {
      if (!path) return '';
      if (/^https?:\/\//i.test(path)) return path;
      var base = MashiaApp.config.apiBase || '';
      if (path.charAt(0) === '/') return base + path;
      return base + '/' + path;
    },

    request: function (path, options) {
      var opts = options || {};
      var headers = {
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest'
      };
      var token = csrfToken();
      if (token) headers['X-CSRFToken'] = token;

      var body = null;
      var method = (opts.method || 'GET').toUpperCase();

      if (opts.data !== undefined && opts.data !== null) {
        var isFormData = (typeof FormData !== 'undefined') && (opts.data instanceof FormData);
        if (isFormData) {
          body = opts.data;
        } else if (opts.raw) {
          body = opts.data;
          headers['Content-Type'] = 'application/json';
        } else {
          body = JSON.stringify(opts.data);
          headers['Content-Type'] = 'application/json';
        }
      }

      var init = {
        method: method,
        headers: headers,
        credentials: opts.credentials || 'same-origin',
        signal: opts.signal
      };
      if (body !== null && method !== 'GET' && method !== 'HEAD') init.body = body;
      if (opts.cache !== undefined) init.cache = opts.cache;

      return fetch(MashiaApp.api.url(path), init).then(function (res) {
        var type = res.headers && res.headers.get ? (res.headers.get('content-type') || '') : '';
        var reader = (type.indexOf('application/json') !== -1) ? res.json() : res.text();
        return Promise.resolve(reader).catch(function () { return null; }).then(function (data) {
          return { ok: res.ok, status: res.status, data: data, headers: res.headers };
        });
      }).catch(function (err) {
        return { ok: false, status: 0, data: null, error: err };
      });
    },

    /** استخراج رسالة خطأ مفهومة من استجابة API (يدعم FastAPI). */
    errorMessage: function (res, fallback) {
      var d = res && res.data;
      if (typeof d === 'string' && d.trim()) return d;
      if (d && typeof d === 'object') {
        if (typeof d.detail === 'string') return d.detail;
        if (typeof d.message === 'string') return d.message;
        if (Array.isArray(d.detail) && d.detail.length) {
          var first = d.detail[0];
          if (first && typeof first === 'object' && first.msg) return first.msg;
        }
        if (Array.isArray(d.errors) && d.errors.length) {
          return d.errors.map(function (x) {
            return (typeof x === 'string') ? x : ((x.field || x.loc || '') + ' ' + (x.message || x.msg || '')).trim();
          }).join('، ');
        }
      }
      if (res && res.status === 0) return 'تعذّر الاتصال بالخادم، تحقّق من الشبكة.';
      if (res && res.status === 401) return 'انتهت الجلسة، يرجى تسجيل الدخول من جديد.';
      if (res && res.status === 403) return 'ليس لديك صلاحية للقيام بهذا الإجراء.';
      return fallback || 'حدث خطأ غير متوقّع.';
    },

    successMessage: function (res, fallback) {
      var d = res && res.data;
      if (d && typeof d === 'object') {
        if (typeof d.message === 'string') return d.message;
        if (typeof d.detail === 'string') return d.detail;
      }
      if (typeof d === 'string' && d.trim() && d.length < 200) return d;
      return fallback || 'تمت العملية بنجاح.';
    }
  };

  /* ==========================================================================
   * 1) Theme — الوضع الفاتح/الليلي
   * ========================================================================== */
  var Theme = {
    STORAGE_KEY: 'mashia-theme',

    /** الوضع المفضّل من إعدادات النظام. */
    preferred: function () {
      try {
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
      } catch (e) { /* غير مدعوم */ }
      return 'light';
    },

    current: function () {
      return doc.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    },

    /** يطبّق الوضع على <html> مع إمكانية الحفظ. */
    apply: function (theme, persist) {
      var value = (theme === 'dark') ? 'dark' : 'light';
      var html = doc.documentElement;
      html.setAttribute('data-theme', value);
      html.style.colorScheme = value;
      var meta = qs('meta[name="theme-color"]');
      if (meta) {
        meta.setAttribute('content', value === 'dark' ? '#0b1220' : '#0f766e');
      }
      if (persist) setStored(Theme.STORAGE_KEY, value);
      Theme.syncToggles();
      try {
        doc.dispatchEvent(new CustomEvent('mashia:theme', { detail: { theme: value } }));
      } catch (e) { /* بيئات قديمة */ }
      return value;
    },

    toggle: function () {
      return Theme.apply(Theme.current() === 'dark' ? 'light' : 'dark', true);
    },

    /** يحدّث نصوص وأيقونات أزرار تبديل الوضع. */
    syncToggles: function () {
      var dark = Theme.current() === 'dark';
      qsa('[data-theme-toggle]').forEach(function (btn) {
        btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
        var icon = btn.querySelector('[data-theme-icon-sun]');
        var moon = btn.querySelector('[data-theme-icon-moon]');
        if (icon) icon.classList.toggle('d-none', !dark);
        if (moon) moon.classList.toggle('d-none', dark);
        var label = btn.querySelector('[data-theme-label]');
        if (label) label.textContent = dark ? 'الوضع النهاري' : 'الوضع الليلي';
        var titled = btn.getAttribute('title') || '';
        if (titled.indexOf('الوضع') === -1) {
          btn.setAttribute('title', dark ? 'تفعيل الوضع النهاري' : 'تفعيل الوضع الليلي');
          btn.setAttribute('aria-label', btn.getAttribute('title'));
        }
      });
    },

    init: function () {
      /* يُستدعى مبكراً (قبل DOMContentLoaded) لمنع وميض الشاشة البيضاء */
      Theme.apply(getStored(Theme.STORAGE_KEY) || Theme.preferred(), false);

      guard('Theme.init', function () {
        doc.addEventListener('click', function (e) {
          var target = e.target && e.target.closest ? e.target.closest('[data-theme-toggle]') : null;
          if (!target) return;
          e.preventDefault();
          Theme.toggle();
        });

        /* مزامنة بين تبويبات المتصفح */
        window.addEventListener('storage', function (e) {
          if (e && e.key === Theme.STORAGE_KEY && e.newValue) Theme.apply(e.newValue, false);
        });

        /* إن لم يحدّد المستخدم خياراً بعد، نتبع تغيّر إعدادات النظام */
        try {
          if (window.matchMedia) {
            var mq = window.matchMedia('(prefers-color-scheme: dark)');
            var handler = function () {
              if (!getStored(Theme.STORAGE_KEY)) Theme.apply(mq.matches ? 'dark' : 'light', false);
            };
            if (typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
            else if (typeof mq.addListener === 'function') mq.addListener(handler);
          }
        } catch (e) { /* غير مدعوم */ }

        Theme.syncToggles();
      });
    }
  };
  MashiaApp.Theme = Theme;

  /* ==========================================================================
   * 2) Sidebar — الشريط الجانبي (درج على الشاشات الصغيرة + طيّ على الكبيرة)
   * ========================================================================== */
  var Sidebar = {
    STORAGE_KEY: 'mashia-sidebar-collapsed',
    $sidebar: null,
    $overlay: null,
    $shell: null,
    $toggle: null,

    isMobile: function () {
      try {
        return window.matchMedia('(max-width: 1023.98px)').matches;
      } catch (e) { return window.innerWidth < 1024; }
    },

    ensureOverlay: function () {
      var overlay = qs('.sidebar-overlay');
      if (!overlay) {
        overlay = doc.createElement('div');
        overlay.className = 'sidebar-overlay';
        overlay.setAttribute('hidden', 'hidden');
        overlay.setAttribute('aria-hidden', 'true');
        doc.body.appendChild(overlay);
      }
      return overlay;
    },

    open: function () {
      if (!Sidebar.$sidebar) return;
      Sidebar.$sidebar.classList.add('is-open', 'open');
      Sidebar.$sidebar.setAttribute('aria-hidden', 'false');
      doc.body.classList.add('sidebar-open');
      if (Sidebar.$overlay) {
        Sidebar.$overlay.removeAttribute('hidden');
        Sidebar.$overlay.classList.add('is-visible');
        Sidebar.$overlay.setAttribute('aria-hidden', 'false');
      }
      if (Sidebar.$toggle) Sidebar.$toggle.setAttribute('aria-expanded', 'true');
      var first = Sidebar.$sidebar.querySelector('a[href], button:not([disabled])');
      if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
      doc.dispatchEvent(new CustomEvent('mashia:sidebar', { detail: { open: true } }));
    },

    close: function () {
      if (!Sidebar.$sidebar) return;
      Sidebar.$sidebar.classList.remove('is-open', 'open');
      Sidebar.$sidebar.setAttribute('aria-hidden', 'true');
      doc.body.classList.remove('sidebar-open');
      if (Sidebar.$overlay) {
        Sidebar.$overlay.classList.remove('is-visible');
        Sidebar.$overlay.setAttribute('aria-hidden', 'true');
        window.setTimeout(function () {
          if (!doc.body.classList.contains('sidebar-open') && Sidebar.$overlay) {
            Sidebar.$overlay.setAttribute('hidden', 'hidden');
          }
        }, 220);
      }
      if (Sidebar.$toggle) Sidebar.$toggle.setAttribute('aria-expanded', 'false');
      doc.dispatchEvent(new CustomEvent('mashia:sidebar', { detail: { open: false } }));
    },

    toggleDrawer: function () {
      if (!Sidebar.$sidebar) return;
      if (Sidebar.isMobile()) {
        if (doc.body.classList.contains('sidebar-open')) Sidebar.close();
        else Sidebar.open();
      } else {
        Sidebar.toggleCollapsed();
      }
    },

    isCollapsed: function () {
      return !!(Sidebar.$sidebar && Sidebar.$sidebar.classList.contains('collapsed'));
    },

    toggleCollapsed: function (force) {
      if (!Sidebar.$sidebar) return;
      var next = (typeof force === 'boolean') ? force : !Sidebar.isCollapsed();
      Sidebar.$sidebar.classList.toggle('collapsed', next);
      if (Sidebar.$shell) Sidebar.$shell.classList.toggle('sidebar-collapsed', next);
      var btn = qs('#sidebarCollapseToggle');
      if (btn) {
        btn.setAttribute('aria-expanded', next ? 'false' : 'true');
        btn.setAttribute('title', next ? 'توسيع القائمة' : 'طيّ القائمة');
      }
      setStored(Sidebar.STORAGE_KEY, next ? '1' : '0');
      try {
        doc.dispatchEvent(new CustomEvent('mashia:sidebar', { detail: { collapsed: next } }));
      } catch (e) { /* ignore */ }
    },

    init: function () {
      var sidebar = qs('.sidebar');
      if (!sidebar) return;

      Sidebar.$sidebar = sidebar;
      Sidebar.$shell = qs('.app-shell');
      Sidebar.$overlay = Sidebar.ensureOverlay();
      Sidebar.$toggle = qs('#sidebarToggle');

      guard('Sidebar.init', function () {
        if (Sidebar.$toggle) {
          Sidebar.$toggle.setAttribute('aria-expanded', 'false');
          Sidebar.$toggle.setAttribute('aria-controls', sidebar.id || 'sidebar');
          Sidebar.$toggle.addEventListener('click', function (e) {
            e.preventDefault();
            Sidebar.toggleDrawer();
          });
        }

        if (Sidebar.$overlay) {
          Sidebar.$overlay.addEventListener('click', function () { Sidebar.close(); });
        }

        /* زر طيّ القائمة على الشاشات الكبيرة */
        qsa('[data-sidebar-collapse], #sidebarCollapseToggle').forEach(function (btn) {
          btn.addEventListener('click', function (e) {
            e.preventDefault();
            if (Sidebar.isMobile()) Sidebar.close();
            else Sidebar.toggleCollapsed();
          });
        });

        /* إغلاق الدرج عند اختيار رابط (على الجوال فقط) */
        qsa('.sidebar a[href]').forEach(function (link) {
          link.addEventListener('click', function () {
            if (Sidebar.isMobile()) Sidebar.close();
          });
        });

        doc.addEventListener('keydown', function (e) {
          if (e.key === 'Escape' && doc.body.classList.contains('sidebar-open')) Sidebar.close();
        });

        /* استعادة حالة الطي المحفوظة (على الشاشات الكبيرة فقط) */
        if (getStored(Sidebar.STORAGE_KEY, '0') === '1' && !Sidebar.isMobile()) {
          sidebar.classList.add('collapsed');
          if (Sidebar.$shell) Sidebar.$shell.classList.add('sidebar-collapsed');
        }

        /* العودة إلى حالة منسدلة عند تغيير حجم النافذة */
        var onResize = debounce(function () {
          if (Sidebar.isMobile()) {
            if (!doc.body.classList.contains('sidebar-open')) sidebar.classList.remove('is-open', 'open');
          }
        }, 180);
        window.addEventListener('resize', onResize);
        window.addEventListener('orientationchange', onResize);
      });
    }
  };
  MashiaApp.Sidebar = Sidebar;

  /* ==========================================================================
   * 3) Modals — النوافذ المنبثقة
   * ========================================================================== */
  var Modals = {
    stack: [],
    $backdrop: null,

    backdrop: function () {
      if (Modals.$backdrop && doc.body.contains(Modals.$backdrop)) return Modals.$backdrop;
      var el = qs('.modal-backdrop');
      if (!el) {
        el = doc.createElement('div');
        el.className = 'modal-backdrop';
        doc.body.appendChild(el);
      }
      Modals.$backdrop = el;
      return el;
    },

    resolve: function (target) {
      if (!target) return null;
      if (typeof target === 'string') {
        return qs('#' + cssEscape(target)) || qs('[data-modal-id="' + target + '"]');
      }
      if (target.nodeType === 1) {
        if (target.classList && target.classList.contains('modal')) return target;
        return target.closest('.modal');
      }
      return null;
    },

    lockScroll: function () {
      var sw = window.innerWidth - doc.documentElement.clientWidth;
      doc.body.classList.add('modal-open');
      if (sw > 0) doc.body.style.paddingInlineEnd = sw + 'px';
    },

    unlockScroll: function () {
      doc.body.classList.remove('modal-open');
      doc.body.style.paddingInlineEnd = '';
    },

    open: function (target, options) {
      var modal = Modals.resolve(target);
      if (!modal) return null;
      var opts = options || {};

      modal.classList.add('is-open', 'show');
      modal.setAttribute('aria-hidden', 'false');
      modal.style.display = 'flex';
      modal.removeAttribute('hidden');
      if (!modal.hasAttribute('role')) modal.setAttribute('role', 'dialog');
      if (!modal.hasAttribute('aria-modal')) modal.setAttribute('aria-modal', 'true');

      var titleEl = modal.querySelector('.modal-title');
      if (titleEl && opts.title) titleEl.textContent = opts.title;

      if (Modals.stack.indexOf(modal) === -1) {
        Modals.stack.push(modal);
        modal._mashiaReturnFocus = doc.activeElement;
      }

      var backdrop = Modals.backdrop();
      backdrop.classList.add('is-open');
      backdrop.style.display = 'block';
      backdrop.removeAttribute('hidden');
      Modals.lockScroll();

      window.setTimeout(function () {
        var focusTarget = modal.querySelector('[data-modal-autofocus]') ||
          modal.querySelector('input:not([type="hidden"]):not([disabled]), select, textarea, button, a[href]');
        if (focusTarget) { try { focusTarget.focus({ preventScroll: true }); } catch (e) { focusTarget.focus(); } }
      }, 60);

      doc.dispatchEvent(new CustomEvent('mashia:modal-open', { detail: { modal: modal } }));
      return modal;
    },

    close: function (target) {
      var modal = Modals.resolve(target);
      if (!modal) return;
      modal.classList.remove('is-open', 'show');
      modal.setAttribute('aria-hidden', 'true');
      modal.style.display = '';

      var idx = Modals.stack.indexOf(modal);
      if (idx !== -1) Modals.stack.splice(idx, 1);

      if (Modals.stack.length === 0) {
        var backdrop = Modals.backdrop();
        backdrop.classList.remove('is-open');
        window.setTimeout(function () {
          if (Modals.stack.length === 0) {
            backdrop.style.display = '';
            backdrop.setAttribute('hidden', 'hidden');
          }
        }, 220);
        Modals.unlockScroll();
      }

      if (modal._mashiaReturnFocus && typeof modal._mashiaReturnFocus.focus === 'function') {
        try { modal._mashiaReturnFocus.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
      }
      modal._mashiaReturnFocus = null;
      doc.dispatchEvent(new CustomEvent('mashia:modal-close', { detail: { modal: modal } }));
    },

    closeAll: function () {
      Modals.stack.slice().forEach(function (m) { Modals.close(m); });
    },

    top: function () {
      return Modals.stack.length ? Modals.stack[Modals.stack.length - 1] : null;
    },

    focusTrap: function (e) {
      if (e.key !== 'Tab') return;
      var modal = Modals.top();
      if (!modal) return;
      var focusables = qsa(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        modal
      ).filter(function (el) { return el.offsetParent !== null || el === doc.activeElement; });
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    },

    init: function () {
      guard('Modals.init', function () {
        Modals.backdrop();

        /* فتح نافذة عبر data-modal-open="id" */
        doc.addEventListener('click', function (e) {
          var opener = e.target && e.target.closest ? e.target.closest('[data-modal-open]') : null;
          if (opener) {
            e.preventDefault();
            Modals.open(opener.getAttribute('data-modal-open'));
            return;
          }

          /* إغلاق عبر data-modal-close (بدون قيمة = أقرب نافذة) */
          var closer = e.target && e.target.closest ? e.target.closest('[data-modal-close], [data-bs-dismiss="modal"]') : null;
          if (closer) {
            e.preventDefault();
            var id = closer.getAttribute('data-modal-close');
            if (id && id !== 'true') Modals.close(id);
            else Modals.close(closer.closest('.modal'));
          }
        });

        /* النقر على الخلفية يغلق النافذة */
        doc.addEventListener('mousedown', function (e) {
          var backdrop = e.target && e.target.closest ? e.target.closest('.modal-backdrop') : null;
          if (backdrop) { Modals.closeAll(); return; }
          var modal = e.target && e.target.closest ? e.target.closest('.modal') : null;
          if (modal && !e.target.closest('.modal-dialog') && !e.target.closest('[data-modal-static]')) {
            Modals.close(modal);
          }
        });

        /* زر الإغلاق العلوي */
        qsa('.modal-close').forEach(function (btn) {
          btn.addEventListener('click', function (e) {
            e.preventDefault();
            Modals.close(btn.closest('.modal'));
          });
        });

        /* Escape + حصر التركيز داخل النافذة */
        doc.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') {
            var top = Modals.top();
            if (top && !top.hasAttribute('data-modal-static')) { e.preventDefault(); Modals.close(top); }
          }
          Modals.focusTrap(e);
        });
      });
    }
  };
  MashiaApp.Modals = Modals;

  /* ==========================================================================
   * 4) Toasts — الإشعارات المؤقتة
   * ========================================================================== */
  var TOAST_ICONS = {
    success: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M16.704 5.292a1 1 0 010 1.414l-7.5 7.5a1 1 0 01-1.414 0l-3.5-3.5a1 1 0 011.414-1.414l2.793 2.793 6.793-6.793a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>',
    danger: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9 5a1 1 0 012 0v5a1 1 0 11-2 0V5zm1 11a1.25 1.25 0 100-2.5 1.25 1.25 0 000 2.5z" clip-rule="evenodd"/></svg>',
    error: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a1 1 0 00-1 1v3a1 1 0 002 0V7a1 1 0 00-1-1zm0 8a1.25 1.25 0 100-2.5 1.25 1.25 0 000 2.5z" clip-rule="evenodd"/></svg>',
    warning: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a1 1 0 00-1 1v3a1 1 0 002 0V7a1 1 0 00-1-1zm0 8a1.25 1.25 0 100-2.5 1.25 1.25 0 000 2.5z" clip-rule="evenodd"/></svg>',
    info: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-4a1 1 0 100 2 1 1 0 000-2zM9 9a1 1 0 012 0v4a1 1 0 11-2 0V9z" clip-rule="evenodd"/></svg>',
    primary: '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-4a1 1 0 100 2 1 1 0 000-2zM9 9a1 1 0 012 0v4a1 1 0 11-2 0V9z" clip-rule="evenodd"/></svg>'
  };

  var TOAST_TITLES = {
    success: 'تمت العملية بنجاح',
    danger: 'تعذّر إتمام العملية',
    error: 'تعذّر إتمام العملية',
    warning: 'تنبيه',
    info: 'معلومة',
    primary: 'إشعار'
  };

  var Toasts = {
    container: function () {
      var el = qs('.toast-container');
      if (!el) {
        el = doc.createElement('div');
        el.className = 'toast-container';
        el.setAttribute('role', 'region');
        el.setAttribute('aria-live', 'polite');
        el.setAttribute('aria-label', 'الإشعارات');
        doc.body.appendChild(el);
      }
      return el;
    },

    show: function (message, type, options) {
      var el = null;
      guard('Toasts.show', function () {
        var opts = options || {};
        var kind = String(type || 'info').toLowerCase();
        if (!TOAST_ICONS[kind]) kind = 'info';

        var timeout = (opts.timeout === undefined || opts.timeout === null)
          ? MashiaApp.config.toastTimeout
          : Number(opts.timeout);

        var host = Toasts.container();
        var toast = doc.createElement('div');
        toast.className = 'toast toast-' + kind;
        toast.setAttribute('role', (kind === 'danger' || kind === 'error') ? 'alert' : 'status');
        toast.setAttribute('aria-live', (kind === 'danger' || kind === 'error') ? 'assertive' : 'polite');

        var title = opts.title || TOAST_TITLES[kind] || '';
        var body = (message === null || message === undefined) ? '' : String(message);
        var icon = opts.icon === false ? '' : (opts.icon || TOAST_ICONS[kind]);

        var html = '';
        if (icon) html += '<div class="toast-icon" aria-hidden="true">' + icon + '</div>';
        html += '<div class="toast-content">';
        if (title) {
          html += '<div class="toast-header"><h4 class="toast-title">' + escapeHtml(title) + '</h4>' +
            '<button type="button" class="toast-close" aria-label="إغلاق">' +
            '<svg viewBox="0 0 20 20" width="14" height="14" fill="currentColor" aria-hidden="true">' +
            '<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"/></svg>' +
            '</button></div>';
        } else {
          html += '<div class="toast-header">' +
            '<button type="button" class="toast-close" aria-label="إغلاق">' +
            '<svg viewBox="0 0 20 20" width="14" height="14" fill="currentColor" aria-hidden="true">' +
            '<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"/></svg>' +
            '</button></div>';
        }
        html += '<div class="toast-body">' + escapeHtml(body) + '</div>';
        if (opts.actionLabel && opts.actionUrl) {
          html += '<div class="toast-body"><a class="btn btn-sm btn-link p-0" href="' + escapeHtml(opts.actionUrl) + '">' +
            escapeHtml(opts.actionLabel) + '</a></div>';
        }
        html += '</div>';
        if (timeout > 0) {
          html += '<div class="toast-progress" style="animation-duration:' + (timeout / 1000) + 's"></div>';
        }

        toast.innerHTML = html;
        host.appendChild(toast);

        var closeBtn = toast.querySelector('.toast-close');
        var timer = null;

        function dismiss() {
          if (!toast.parentNode) return;
          window.clearTimeout(timer);
          toast.classList.add('is-leaving');
          window.setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
          }, 220);
          doc.dispatchEvent(new CustomEvent('mashia:toast-closed', { detail: { type: kind } }));
        }

        if (closeBtn) closeBtn.addEventListener('click', dismiss);
        toast.addEventListener('mouseenter', function () { window.clearTimeout(timer); });
        toast.addEventListener('mouseleave', function () {
          if (timeout > 0) timer = window.setTimeout(dismiss, 1500);
        });

        if (timeout > 0) timer = window.setTimeout(dismiss, timeout);

        if (typeof opts.onClose === 'function') toast.addEventListener('click', function () {
          window.setTimeout(function () { guard('Toasts.onClose', opts.onClose); }, 0);
        });

        el = toast;
        doc.dispatchEvent(new CustomEvent('mashia:toast', { detail: { type: kind, message: body } }));
      });
      return el;
    },

    success: function (m, o) { return Toasts.show(m, 'success', o); },
    error: function (m, o) { return Toasts.show(m, 'danger', o); },
    danger: function (m, o) { return Toasts.show(m, 'danger', o); },
    warning: function (m, o) { return Toasts.show(m, 'warning', o); },
    info: function (m, o) { return Toasts.show(m, 'info', o); },

    dismissAll: function () {
      guard('Toasts.dismissAll', function () {
        qsa('.toast').forEach(function (t) {
          t.classList.add('is-leaving');
          window.setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 220);
        });
      });
    },

    init: function () {
      guard('Toasts.init', function () {
        /* أزرار الإغلاق في التنبيهات الثابتة */
        doc.addEventListener('click', function (e) {
          var btn = e.target && e.target.closest ? e.target.closest('.alert-close, [data-dismiss-alert]') : null;
          if (!btn) return;
          var alert = btn.closest('.alert');
          if (!alert) return;
          e.preventDefault();
          alert.classList.add('is-leaving');
          window.setTimeout(function () {
            if (alert.parentNode) alert.parentNode.removeChild(alert);
          }, 220);
        });

        /* إخفاء تلقائي للتنبيهات التي تحمل data-auto-dismiss */
        qsa('.alert[data-auto-dismiss]').forEach(function (alert) {
          var delay = parseInt(alert.getAttribute('data-auto-dismiss'), 10);
          if (isNaN(delay) || delay <= 0) delay = 6000;
          window.setTimeout(function () {
            alert.classList.add('is-leaving');
            window.setTimeout(function () { if (alert.parentNode) alert.parentNode.removeChild(alert); }, 250);
          }, delay);
        });
      });
    }
  };
  MashiaApp.Toasts = Toasts;

  /* ==========================================================================
   * 5) Forms — التحقق، حالة التحميل، والإرسال عبر AJAX
   * ========================================================================== */
  var Forms = {
    MESSAGES: {
      required: 'هذا الحقل مطلوب.',
      email: 'يرجى إدخال بريد إلكتروني صحيح.',
      number: 'يرجى إدخال رقم صحيح.',
      url: 'يرجى إدخال رابط صحيح.',
      min: 'أدخل قيمة أكبر أو تساوي {v}.',
      max: 'أدخل قيمة أصغر أو تساوي {v}.',
      minlength: 'أدخل {v} حرفاً على الأقل.',
      maxlength: 'أدخل {v} حرفاً كحد أقصى.',
      pattern: 'صيغة المدخل غير صحيحة.',
      checked: 'يجب تحديد هذا الخيار.',
      mismatch: 'القيمتان غير متطابقتين.'
    },

    message: function (key, value) {
      var msg = Forms.MESSAGES[key] || '';
      if (value !== undefined && value !== null && value !== '') {
        msg = msg.replace('{v}', value);
      }
      return msg;
    },

    errorBox: function (control) {
      var group = control.closest('.form-group, .col, .input-group, .form-check') || control.parentElement;
      if (!group) return null;
      var box = group.querySelector('.form-error');
      if (!box) {
        box = doc.createElement('div');
        box.className = 'form-error';
        box.setAttribute('role', 'alert');
        if (control.classList.contains('form-check-input')) group.appendChild(box);
        else if (control.parentElement && control.parentElement.classList.contains('input-group')) {
          group.appendChild(box);
        } else {
          control.parentNode.insertBefore(box, control.nextSibling);
        }
      }
      return box;
    },

    setError: function (control, message) {
      if (!control) return;
      control.classList.add('is-invalid');
      control.setAttribute('aria-invalid', 'true');
      var box = Forms.errorBox(control);
      if (box) {
        box.textContent = message;
        box.classList.add('is-visible');
      }
    },

    clearError: function (control) {
      if (!control) return;
      control.classList.remove('is-invalid');
      control.removeAttribute('aria-invalid');
      var box = Forms.errorBox(control);
      if (box) {
        box.textContent = '';
        box.classList.remove('is-visible');
      }
    },

    /** التحقق من حقل واحد. يُرجع رسالة الخطأ أو نص فارغ عند النجاح. */
    check: function (control) {
      if (!control || control.disabled || control.type === 'hidden') return '';

      var value = (control.type === 'checkbox') ? control.checked : control.value;
      var required = control.hasAttribute('required') ||
        control.getAttribute('data-required') === 'true' ||
      control.required === true;
      var customRequired = control.getAttribute('data-required-message');
      var valueText = typeof value === 'string' ? value.trim() : value;

      if (required && (control.type === 'checkbox' ? !value : !valueText)) {
        return customRequired || Forms.message('required');
      }

      if (control.type === 'email' && valueText && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(valueText)) {
        return control.getAttribute('data-error-message') || Forms.message('email');
      }
      if (control.type === 'url' && valueText && !/^https?:\/\/.+\..+/.test(valueText)) {
        return control.getAttribute('data-error-message') || Forms.message('url');
      }
      if (control.type === 'number' || control.getAttribute('type') === 'number') {
        if (valueText !== '' && isNaN(normalizeNumber(valueText))) {
          return control.getAttribute('data-error-message') || Forms.message('number');
        }
        var num = normalizeNumber(valueText);
        if (valueText !== '' && !isNaN(num)) {
          if (control.min !== '' && num < parseFloat(control.min)) return Forms.message('min', control.min);
          if (control.max !== '' && num > parseFloat(control.max)) return Forms.message('max', control.max);
        }
      }
      if (control.pattern && valueText) {
        try {
          var re = new RegExp('^(?:' + control.pattern + ')$');
          if (!re.test(valueText)) return Forms.message('pattern');
        } catch (err) { /* نمط غير صالح */ }
      }
      if (control.minLength > 0 && String(valueText).length < control.minLength) {
        return Forms.message('minlength', control.minLength);
      }
      if (control.maxLength > 0 && String(valueText).length > control.maxLength) {
        return Forms.message('maxlength', control.maxLength);
      }

      /* حقول التأكيد: data-match="#id" */
      var match = control.getAttribute('data-match');
      if (match && valueText) {
        var other = qs(match);
        if (other && other.value !== valueText) return Forms.message('mismatch');
      }

      return '';
    },

    /** يتحقق من كل حقول النموذج ويعرض الأخطاء. */
    validate: function (form) {
      if (!form) return true;
      var ok = true;
      var firstInvalid = null;
      qsa('input, select, textarea', form).forEach(function (control) {
        if (control.type === 'hidden' || control.name === '_csrf' || control.disabled) return;
        if (control.closest('[data-no-validate]')) return;
        var msg = Forms.check(control);
        if (msg) {
          Forms.setError(control, msg);
          ok = false;
          if (!firstInvalid) firstInvalid = control;
        } else {
          Forms.clearError(control);
          if (control.classList.contains('form-control') || control.classList.contains('form-select')) {
            control.classList.add('is-valid');
          }
        }
      });
      if (firstInvalid) {
        try { firstInvalid.focus({ preventScroll: true }); firstInvalid.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
        catch (e) { try { firstInvalid.focus(); } catch (e2) { /* ignore */ } }
      }
      return ok;
    },

    submitButton: function (form) {
      return form.querySelector('button[type="submit"], input[type="submit"], button:not([type])[data-submit]');
    },

    setLoading: function (button, loading) {
      if (!button) return;
      if (loading) {
        if (button.dataset.mashiaLoading === '1') return;
        button.dataset.mashiaLoading = '1';
        button.dataset.mashiaLabel = button.innerHTML;
        button.classList.add('is-loading');
        button.setAttribute('aria-busy', 'true');
        button.disabled = true;
      } else {
        button.classList.remove('is-loading');
        button.removeAttribute('aria-busy');
        button.disabled = false;
        delete button.dataset.mashiaLoading;
      }
    },

    serialize: function (form) {
      var data = {};
      qsa('input, select, textarea', form).forEach(function (control) {
        if (!control.name || control.disabled || control.type === 'submit' || control.type === 'button') return;
        if (control.type === 'checkbox') {
          if (control.checked) {
            if (control.getAttribute('data-multi') === 'list') {
              if (!data[control.name]) data[control.name] = [];
              data[control.name].push(control.value);
            } else {
              data[control.name] = control.value || 'on';
            }
          }
          return;
        }
        if (control.type === 'radio' && !control.checked) return;
        if (control.multiple && control.tagName === 'SELECT') {
          data[control.name] = Array.prototype.slice.call(control.selectedOptions).map(function (o) { return o.value; });
          return;
        }
        data[control.name] = control.value;
      });
      return data;
    },

    send: function (form, submitter) {
      var method = (form.getAttribute('method') || 'POST').toUpperCase();
      var url = form.getAttribute('action') || window.location.href;
      var opts = form.dataset || {};
      var payload = Forms.serialize(form);
      if (submitter && submitter.name && !payload[submitter.name]) payload[submitter.name] = submitter.value;

      var asJson = (opts.ajax !== undefined) ||
        form.hasAttribute('data-ajax') ||
        String(form.getAttribute('enctype') || '').indexOf('json') !== -1;

      var requestOptions = { method: method === 'GET' ? 'GET' : method };
      if (method !== 'GET') {
        if (asJson) {
          requestOptions.data = payload;
        } else {
          var fd = new FormData(form);
          if (submitter && submitter.name) fd.append(submitter.name, submitter.value);
          requestOptions.data = fd;
        }
      }

      return MashiaApp.api.request(url, requestOptions).then(function (res) {
        if (res.ok) {
          var message = MashiaApp.api.successMessage(res, opts.successMessage || 'تم حفظ البيانات بنجاح.');
          Toasts.show(message, 'success');
          doc.dispatchEvent(new CustomEvent('mashia:form-success', { detail: { form: form, data: res.data } }));

          var redirect = opts.redirect;
          if (!redirect && res.data && typeof res.data === 'object') {
            redirect = res.data.redirect || res.data.location || null;
          }
          if (redirect) {
            window.setTimeout(function () { window.location.href = redirect; }, 400);
          } else if (opts.reset !== 'false' && form.dataset.reset !== 'false') {
            form.reset();
          }
          return { ok: true, data: res.data };
        }

        var msg = MashiaApp.api.errorMessage(res, opts.errorMessage || 'تعذّر حفظ البيانات.');
        Toasts.show(msg, 'danger', { timeout: 8000 });

        /* ربط أخطاء الحقول القادمة من الخادم (FastAPI 422) */
        var details = res.data && res.data.detail;
        if (Array.isArray(details)) {
          details.forEach(function (item) {
            if (!item || !item.loc) return;
            var field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : item.loc;
            var control = form.querySelector('[name="' + field + '"], [data-field="' + field + '"]');
            if (control) Forms.setError(control, item.msg || msg);
          });
        }
        return { ok: false, data: res.data };
      });
    },

    init: function () {
      guard('Forms.init', function () {
        doc.addEventListener('submit', function (e) {
          var form = e.target;
          if (!form || form.tagName !== 'FORM') return;
          if (form.hasAttribute('data-no-submit-handler')) return;
          if (form.dataset.mashiaSubmitting === '1') { e.preventDefault(); return; }

          var submitter = e.submitter || Forms.submitButton(form);
          var valid = Forms.validate(form);

          if (!valid) {
            e.preventDefault();
            Toasts.show('يرجى تصحيح الحقول المعلَّمة بالأحمر.', 'warning');
            return;
          }

          if (submitter && submitter.hasAttribute('data-confirm')) return; /* يتكفّل به Confirm */

          /* إرسال غير متزامن عبر AJAX */
          if (form.hasAttribute('data-ajax') || form.dataset.ajax !== undefined) {
            e.preventDefault();
            if (form.dataset.mashiaSubmitting === '1') return;
            form.dataset.mashiaSubmitting = '1';
            Forms.setLoading(submitter, true);
            Forms.send(form, submitter).then(function (result) {
              delete form.dataset.mashiaSubmitting;
              Forms.setLoading(submitter, false);
              if (!result.ok && submitter && submitter.hasAttribute('data-focus-on-error')) {
                try { submitter.focus(); } catch (err) { /* ignore */ }
              }
            });
            return;
          }

          /* نموذج عادي: نضع حالة تحميل قبل الإرسال (للإرشاد فقط) */
          if (submitter && form.dataset.loadingState !== 'false') {
            Forms.setLoading(submitter, true);
            window.setTimeout(function () { Forms.setLoading(submitter, false); }, 12000);
          }
        });

        /* إزالة رسالة الخطأ فور تصحيح الحقل */
        doc.addEventListener('input', function (e) {
          var control = e.target;
          if (control && control.classList && control.classList.contains('is-invalid')) {
            if (!Forms.check(control)) {
              Forms.clearError(control);
              control.classList.add('is-valid');
            }
          }
        });
        doc.addEventListener('blur', function (e) {
          var control = e.target;
          if (control && control.classList && control.classList.contains('is-invalid')) Forms.check(control);
        }, true);

        /* منع الضغط المزدوج على أزرار الإرسال */
        qsa('[data-disable-on-submit]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var form = btn.closest('form');
            if (form) window.setTimeout(function () { Forms.setLoading(btn, true); }, 0);
          });
        });

        /* تلميح التحميل حتى انتهاء الإرسال إلى الخادم */
        qsa('[data-loading-text]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var span = btn.querySelector('.btn-text');
            if (span && !span.dataset.original) span.dataset.original = span.textContent;
            var t = btn.getAttribute('data-loading-text');
            if (span && t) span.textContent = t;
          });
        });
      });
    }
  };
  MashiaApp.Forms = Forms;

  /* ==========================================================================
   * 6) Confirm — نافذة تأكيد العمليات الحساسة
   * ========================================================================== */
  var Confirm = {
    MODAL_ID: 'mashiaConfirmModal',

    build: function () {
      var existing = doc.getElementById(Confirm.MODAL_ID);
      if (existing) return existing;

      var wrap = doc.createElement('div');
      wrap.className = 'modal';
      wrap.id = Confirm.MODAL_ID;
      wrap.setAttribute('tabindex', '-1');
      wrap.setAttribute('aria-hidden', 'true');
      wrap.setAttribute('role', 'dialog');
      wrap.setAttribute('aria-modal', 'true');
      wrap.innerHTML = [
        '<div class="modal-dialog modal-dialog-centered modal-sm" role="document">',
        '  <div class="modal-content">',
        '    <div class="modal-header">',
        '      <h5 class="modal-title" data-confirm-title>تأكيد العملية</h5>',
        '      <button type="button" class="modal-close" data-modal-close aria-label="إغلاق">&#10005;</button>',
        '    </div>',
        '    <div class="modal-body">',
        '      <p class="mb-0" data-confirm-text>هل أنت متأكد من تنفيذ هذه العملية؟</p>',
        '    </div>',
        '    <div class="modal-footer">',
        '      <button type="button" class="btn btn-primary" data-confirm-ok data-modal-autofocus>تأكيد</button>',
        '      <button type="button" class="btn btn-outline-secondary" data-confirm-cancel>إلغاء</button>',
        '    </div>',
        '  </div>',
        '</div>'
      ].join('');

      doc.body.appendChild(wrap);
      return wrap;
    },

    /** يعرض نافذة تأكيد ويعيد Promise<boolean>. */
    request: function (options) {
      return new Promise(function (resolve) {
        guard('Confirm.request', function () {
          var opts = options || {};
          var modal = Confirm.build();
          var titleEl = modal.querySelector('[data-confirm-title]');
          var textEl = modal.querySelector('[data-confirm-text]');
          var okBtn = modal.querySelector('[data-confirm-ok]');
          var cancelBtn = modal.querySelector('[data-confirm-cancel]');

          titleEl.textContent = opts.title || 'تأكيد العملية';
          textEl.textContent = opts.text || opts.message || 'هل أنت متأكد من تنفيذ هذه العملية؟';
          okBtn.textContent = opts.confirmText || 'تأكيد';
          cancelBtn.textContent = opts.cancelText || 'إلغاء';
          okBtn.className = 'btn ' + (opts.variant || 'btn-danger');

          var settled = false;
          function finish(result) {
            if (settled) return;
            settled = true;
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('mashia:modal-close', onClose);
            Modals.close(modal);
            resolve(result);
          }
          function onOk() { finish(true); }
          function onCancel() { finish(false); }
          function onClose() { finish(false); }

          okBtn.addEventListener('click', onOk);
          cancelBtn.addEventListener('click', onCancel);
          modal.addEventListener('mashia:modal-close', onClose);

          Modals.open(modal);
        });
      });
    },

    /** تنفيذ الإجراء المرتبط بالعنصر بعد التأكيد. */
    perform: function (element) {
      if (!element) return;

      var target = element.getAttribute('data-confirm-href');
      var form = element.tagName === 'BUTTON' ? element.closest('form') : element.closest('form');

      if (target) {
        window.location.href = target;
        return;
      }
      if (element.tagName === 'A' && element.getAttribute('href') && element.getAttribute('href') !== '#') {
        window.location.href = element.getAttribute('href');
        return;
      }
      if (form && element.tagName === 'BUTTON') {
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit(element.getAttribute('name') ? element : undefined);
        } else {
          element.removeAttribute('disabled');
          form.submit();
        }
        return;
      }

      doc.dispatchEvent(new CustomEvent('mashia:confirmed', {
        detail: { element: element }
      }));
    },

    init: function () {
      guard('Confirm.init', function () {
        doc.addEventListener('click', function (e) {
          var el = e.target && e.target.closest ? e.target.closest('[data-confirm]') : null;
          if (!el) return;
          if (el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true') return;
          /* تجاهل النقر على عنصر التأكيد نفسه داخل نافذة التأكيد */
          if (el.closest('#' + Confirm.MODAL_ID)) return;

          e.preventDefault();
          e.stopPropagation();

          var message = el.getAttribute('data-confirm-text');
          if (el.tagName === 'FORM' || (el.tagName !== 'A' && !message)) message = null;

          Confirm.request({
            title: el.getAttribute('data-confirm-title') || 'تأكيد العملية',
            text: message || 'هل أنت متأكد من تنفيذ هذه العملية؟ لا يمكن التراجع عنها.',
            confirmText: el.getAttribute('data-confirm-button') || 'تأكيد',
            cancelText: el.getAttribute('data-confirm-cancel') || 'إلغاء',
            variant: el.getAttribute('data-confirm-variant') || 'btn-danger'
          }).then(function (ok) {
            if (ok) Confirm.perform(el);
          });
        }, true);
      });
    }
  };
  MashiaApp.Confirm = Confirm;

  /* ==========================================================================
   * 7) Attendance — تسجيل الحضور عبر QR أو إدخال يدوي
   * ========================================================================== */
  var Attendance = {
    MODAL_ID: 'mashiaScannerModal',
    stream: null,
    rafId: null,
    lastCode: '',
    lastCodeAt: 0,

    barcodeSupported: function () {
      return (typeof window.BarcodeDetector === 'function');
    },

    endpoint: function (sessionId) {
      return MashiaApp.config.attendanceEndpoint.replace('{id}', encodeURIComponent(sessionId));
    },

    /** إرسال كود الحضور إلى الخادم. */
    submit: function (sessionId, code) {
      return MashiaApp.api.request(Attendance.endpoint(sessionId), {
        method: 'POST',
        data: { code: String(code || '').trim(), session_id: toNumber(sessionId, null) }
      });
    },

    /** ماسح QR بالكاميرا. يعيد Promise بقيمة الكود المقروء. */
    scan: function (options) {
      var opts = options || {};

      return new Promise(function (resolve) {
        guard('Attendance.scan', function () {
          var videoId = opts.videoId || 'mashiaScannerVideo';
          var statusId = opts.statusId || 'mashiaScannerStatus';
          var video = qs('#' + cssEscape(videoId)) || qs('video[data-qr-video]');

          if (!video) {
            Toasts.show('عنصر الكاميرا غير موجود في الصفحة.', 'danger');
            resolve('');
            return;
          }

          var status = qs('#' + cssEscape(statusId));
          function setStatus(text, tone) {
            if (!status) return;
            status.textContent = text;
            status.className = 'text-sm ' + (tone || 'text-muted');
          }

          if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus('المتصفح لا يدعم الوصول إلى الكاميرا. استخدم الإدخال اليدوي بدلاً من ذلك.', 'text-danger');
            resolve('');
            return;
          }
          if (!Attendance.barcodeSupported()) {
            setStatus('قراءة رموز QR غير مدعومة في هذا المتصفح. استعمل الإدخال اليدوي للرمز.', 'text-warning');
            resolve('');
            return;
          }

          var detector;
          try {
            detector = new window.BarcodeDetector({
              formats: ['qr_code', 'code_128', 'code_39', 'ean_13', 'data_matrix']
            });
          } catch (err) {
            try { detector = new window.BarcodeDetector(); }
            catch (err2) {
              setStatus('تعذّر تهيئة قارئ الرموز.', 'text-danger');
              resolve('');
              return;
            }
          }

          setStatus('جارٍ تشغيل الكاميرا…', 'text-muted');

          navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: false
          }).then(function (stream) {
            Attendance.stream = stream;
            video.srcObject = stream;
            video.setAttribute('playsinline', '');
            video.setAttribute('muted', '');
            return video.play();
          }).then(function () {
            setStatus('وجّه الكاميرا نحو بطاقة الرياضي…', 'text-primary');
            var busy = false;

            function tick() {
              Attendance.rafId = window.requestAnimationFrame(tick);
              if (busy || !video.videoWidth) return;
              busy = true;
              detector.detect(video)
                .then(function (codes) {
                  if (!codes || !codes.length) return;
                  var value = String(codes[0].rawValue || '').trim();
                  if (!value) return;
                  var now = Date.now();
                  if (value === Attendance.lastCode && (now - Attendance.lastCodeAt) < 2500) return;
                  Attendance.lastCode = value;
                  Attendance.lastCodeAt = now;
                  setStatus('تم قراءة الرمز: ' + value, 'text-success');
                  try { navigator.vibrate && navigator.vibrate(120); } catch (e) { /* ignore */ }
                  Attendance.stop();
                  if (typeof opts.onDetected === 'function') {
                    guard('Attendance.onDetected', function () { opts.onDetected(value, codes[0]); });
                  }
                  resolve(value);
                })
                .catch(function () { /* إطار غير صالح */ })
                .then(function () { busy = false; });
            }
            tick();
          }).catch(function (err) {
            var msg = 'تعذّر فتح الكاميرا. تحقّق من أذونات المتصفح ثم أعد المحاولة.';
            if (err && err.name === 'NotAllowedError') msg = 'تم رفض إذن الكاميرا. فعّله من إعدادات المتصفح.';
            if (err && err.name === 'NotFoundError') msg = 'لم يتم العثور على كاميرا في هذا الجهاز.';
            setStatus(msg, 'text-danger');
            Toasts.show(msg, 'warning', { timeout: 7000 });
            resolve('');
          });
        });
      });
    },

    /** يوقف الكاميرا ويحرّر الموارد. */
    stop: function () {
      if (Attendance.rafId) {
        window.cancelAnimationFrame(Attendance.rafId);
        Attendance.rafId = null;
      }
      if (Attendance.stream) {
        Attendance.stream.getTracks().forEach(function (track) {
          try { track.stop(); } catch (e) { /* ignore */ }
        });
        Attendance.stream = null;
      }
      qsa('video[data-qr-video], #mashiaScannerVideo').forEach(function (v) {
        try { v.pause(); v.srcObject = null; } catch (e) { /* ignore */ }
      });
    },

    /** ينشئ نافذة الماسح (تُستدعى من الواجهة عند الحاجة). */
    openScanner: function (options) {
      var opts = options || {};
      guard('Attendance.openScanner', function () {
        var modal = qs('#mashiaScannerModal');
        if (!modal) {
          modal = doc.createElement('div');
          modal.className = 'modal';
          modal.id = Attendance.MODAL_ID;
          modal.setAttribute('tabindex', '-1');
          modal.setAttribute('aria-hidden', 'true');
          modal.innerHTML = [
            '<div class="modal-dialog modal-lg" role="document">',
            '  <div class="modal-content">',
            '    <div class="modal-header">',
            '      <h5 class="modal-title">مسح بطاقة الحضور (QR)</h5>',
            '      <button type="button" class="modal-close" data-modal-close aria-label="إغلاق">&#10005;</button>',
            '    </div>',
            '    <div class="modal-body">',
            '      <div class="scanner-frame position-relative rounded-lg overflow-hidden mb-3">',
            '        <video id="mashiaScannerVideo" data-qr-video playsinline muted class="w-100"></video>',
            '      </div>',
            '      <p id="mashiaScannerStatus" class="text-center text-muted text-sm mb-0"></p>',
            '    </div>',
            '    <div class="modal-footer">',
            '      <button type="button" class="btn btn-primary" data-modal-close>إغلاق</button>',
            '    </div>',
            '  </div>',
            '</div>'
          ].join('');
          doc.body.appendChild(modal);
          modal.addEventListener('mashia:modal-close', function () { Attendance.stop(); });
        }

        Modals.open(modal);
        Attendance.scan(opts);
      });
    },

    /** يعالج نتيجة تسجيل الحضور ويعرض إشعاراً مناسباً. */
    handleResult: function (res, code) {
      if (res.ok) {
        var msg = MashiaApp.api.successMessage(res, 'تم تسجيل الحضور بنجاح: ' + code);
        Toasts.show(msg, 'success');
        doc.dispatchEvent(new CustomEvent('mashia:attendance', { detail: { code: code, data: res.data } }));
        return true;
      }
      Toasts.show(MashiaApp.api.errorMessage(res, 'تعذّر تسجيل الحضور للرمز ' + code), 'danger', { timeout: 8000 });
      doc.dispatchEvent(new CustomEvent('mashia:attendance-error', { detail: { code: code, data: res.data } }));
      return false;
    },

    init: function () {
      guard('Attendance.init', function () {
        /* زر فتح الماسح */
        qsa('[data-attendance-scan]').forEach(function (btn) {
          btn.addEventListener('click', function (e) {
            e.preventDefault();
            Attendance.openScanner({
              sessionId: btn.getAttribute('data-attendance-scan') || btn.getAttribute('data-session-id'),
              onDetected: function (code) {
                var sessionId = btn.getAttribute('data-attendance-scan') || btn.getAttribute('data-session-id');
                if (sessionId) Attendance.submit(sessionId, code).then(function (res) { Attendance.handleResult(res, code); });
                Modals.close(qs('#' + Attendance.MODAL_ID));
              }
            });
          });
        });

        /* نموذج الإدخال اليدوي */
        qsa('form[data-attendance-form]').forEach(function (form) {
          form.addEventListener('submit', function (e) {
            e.preventDefault();
            var sessionId = form.getAttribute('data-session-id') ||
              (form.querySelector('[name="session_id"]') || {}).value;
            var input = form.querySelector('[name="code"], input[data-code-input]');
            var code = input ? input.value.trim() : '';

            if (!sessionId) { Toasts.show('معرّف الحصة مفقود.', 'danger'); return; }
            if (!code) {
              if (input) {
                input.classList.add('is-invalid');
                Forms.setError(input, 'يرجى إدخال رمز البطاقة.');
              }
              Toasts.show('يرجى إدخال رمز البطاقة.', 'warning');
              return;
            }
            if (input) { Forms.clearError(input); input.classList.remove('is-invalid'); }

            var btn = form.querySelector('button[type="submit"]') || Forms.submitButton(form);
            Forms.setLoading(btn, true);
            Attendance.submit(sessionId, code).then(function (res) {
              Forms.setLoading(btn, false);
              if (Attendance.handleResult(res, code) && input) input.value = '';
            });
          });
        });

        /* تنظيف الكاميرا عند مغادرة الصفحة */
        window.addEventListener('pagehide', function () { Attendance.stop(); });
        window.addEventListener('beforeunload', function () { Attendance.stop(); });
      });
    }
  };
  MashiaApp.Attendance = Attendance;

  /* ==========================================================================
   * 8) Notifications — عدّاد الإشعارات وقائمتها
   * ========================================================================== */
  var Notifications = {
    COUNT_URL: '/api/v1/notifications/unread-count',
    LIST_URL: '/api/v1/notifications',
    timer: null,

    extractCount: function (res) {
      var d = res && res.data;
      if (typeof d === 'number') return d;
      if (typeof d === 'string') return toNumber(d, 0);
      if (d && typeof d === 'object') {
        var keys = ['count', 'unread_count', 'unread', 'total', 'data'];
        for (var i = 0; i < keys.length; i++) {
          if (d[keys[i]] !== undefined && d[keys[i]] !== null) return toNumber(d[keys[i]], 0);
        }
      }
      return 0;
    },

    extractList: function (res) {
      var d = res && res.data;
      if (Array.isArray(d)) return d;
      if (d && typeof d === 'object') {
        if (Array.isArray(d.items)) return d.items;
        if (Array.isArray(d.results)) return d.results;
        if (Array.isArray(d.notifications)) return d.notifications;
        if (Array.isArray(d.data)) return d.data;
      }
      return [];
    },

    /** يحدّث شارات [data-notification-count]. */
    setCount: function (count) {
      var value = Math.max(0, toNumber(count, 0));
      qsa('[data-notification-count]').forEach(function (el) {
        el.textContent = value > 99 ? '99+' : String(value);
        el.classList.toggle('d-none', value === 0);
        el.setAttribute('aria-label', value + ' إشعار غير مقروء');
        var dot = el.closest('[data-notification-wrapper]');
        if (dot) dot.classList.toggle('has-unread', value > 0);
      });
      doc.dispatchEvent(new CustomEvent('mashia:notifications-count', { detail: { count: value } }));
      return value;
    },

    refresh: function () {
      return MashiaApp.api.request(Notifications.COUNT_URL, { method: 'GET' }).then(function (res) {
        if (!res.ok) return null;
        return Notifications.setCount(Notifications.extractCount(res));
      });
    },

    timeAgo: function (iso) {
      if (!iso) return '';
      var then = Date.parse(iso);
      if (isNaN(then)) return '';
      var diff = Math.floor((Date.now() - then) / 1000);
      if (diff < 0) diff = 0;
      if (diff < 60) return 'الآن';
      if (diff < 3600) return 'قبل ' + Math.floor(diff / 60) + ' دقيقة';
      if (diff < 86400) return 'قبل ' + Math.floor(diff / 3600) + ' ساعة';
      if (diff < 604800) return 'قبل ' + Math.floor(diff / 86400) + ' يوم';
      try {
        return new Intl.DateTimeFormat(MashiaApp.config.locale, { dateStyle: 'short' }).format(new Date(then));
      } catch (e) { return new Date(then).toLocaleDateString(); }
    },

    render: function (items) {
      var targets = qsa('[data-notification-list]');
      if (!targets.length) return;

      targets.forEach(function (host) {
        if (!items.length) {
          host.innerHTML = '<div class="empty-state empty-state-sm">' +
            '<span class="empty-state-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="28" height="28">' +
            '<path d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.7V5a2 2 0 10-4 0v.3A6 6 0 006 11v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>' +
            '</span>' +
            '<p class="empty-state-text mb-0">لا توجد إشعارات جديدة.</p>' +
            '</div>';
          return;
        }

        var html = items.map(function (n) {
          var n2 = n || {};
          var title = escapeHtml(n2.title || n2.subject || 'إشعار');
          var body = escapeHtml(n2.message || n2.body || n2.description || '');
          var time = escapeHtml(Notifications.timeAgo(n2.created_at || n2.created || n2.timestamp));
          var href = n2.url || n2.link || '#';
          var unread = n2.read === false || n2.is_read === false || n2.unread === true;
          var icon = n2.icon ? escapeHtml(n2.icon) : '●';
          return '<a class="notification-item' + (unread ? ' unread' : '') + '" href="' + escapeHtml(href) + '">' +
            '<span class="notification-icon" aria-hidden="true">' + icon + '</span>' +
            '<span class="notification-text">' +
            '<span class="d-block fw-semibold">' + title + '</span>' +
            '<span class="d-block text-muted">' + body + '</span>' +
            '<span class="notification-time d-block">' + time + '</span>' +
            '</span></a>';
        }).join('');

        host.innerHTML = html;
      });
    },

    load: function (limit) {
      return MashiaApp.api.request(Notifications.LIST_URL + '?limit=' + (limit || 5), { method: 'GET' })
        .then(function (res) {
          if (!res.ok) {
            var host = qs('[data-notification-list]');
            if (host) host.innerHTML = '<div class="p-3 text-center text-muted text-sm">تعذّر تحميل الإشعارات.</div>';
            return [];
          }
          var items = Notifications.extractList(res);
          Notifications.render(items);
          return items;
        });
    },

    start: function () {
      Notifications.refresh();

      if (Notifications.timer) window.clearInterval(Notifications.timer);
      Notifications.timer = window.setInterval(function () {
        if (doc.hidden) return;
        Notifications.refresh();
      }, MashiaApp.config.notificationsInterval);

      /* تحميل القائمة عند فتح القائمة المنسدلة */
      qsa('[data-notifications-toggle]').forEach(function (toggle) {
        toggle.addEventListener('click', function () {
          window.setTimeout(function () { Notifications.load(5); }, 120);
        });
      });
    },

    stop: function () {
      if (Notifications.timer) {
        window.clearInterval(Notifications.timer);
        Notifications.timer = null;
      }
    },

    init: function () {
      guard('Notifications.init', function () {
        if (!qs('[data-notification-count]') && !qs('[data-notification-list]')) return;
        Notifications.start();
        doc.addEventListener('visibilitychange', function () {
          if (!doc.hidden) Notifications.refresh();
        });
      });
    }
  };
  MashiaApp.Notifications = Notifications;

  /* ==========================================================================
   * 9) Filters — إرسال تلقائي للنماذج عند تغيير الفلاتر
   * ========================================================================== */
  var Filters = {
    submit: function (form) {
      if (!form) return;
      var btn = Forms.submitButton(form);
      Forms.setLoading(btn, true);
      window.setTimeout(function () {
        if (typeof form.requestSubmit === 'function') {
          try { form.requestSubmit(); } catch (e) { form.submit(); }
        } else {
          form.submit();
        }
      }, 60);
    },

    init: function () {
      guard('Filters.init', function () {
        qsa('form[data-auto-submit]').forEach(function (form) {
          var run = debounce(function () { Filters.submit(form); },
            parseInt(form.getAttribute('data-auto-submit-delay'), 10) || MashiaApp.config.filtersDebounce);

          form.addEventListener('change', function (e) {
            var tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'select' || tag === 'input' || tag === 'textarea') run();
          });

          /* زر "بحث" اختياري */
          var search = form.querySelector('[data-filter-submit]');
          if (search) search.addEventListener('click', function (e) { e.preventDefault(); Filters.submit(form); });

          /* زر إعادة التعيين */
          var reset = form.querySelector('[data-filter-reset]');
          if (reset) {
            reset.addEventListener('click', function () {
              window.setTimeout(function () { Filters.submit(form); }, 0);
            });
          }
        });
      });
    }
  };
  MashiaApp.Filters = Filters;

  /* ==========================================================================
   * 10) Charts — رسوم بيانية بسيطة بدون أي مكتبات
   * ========================================================================== */
  var Charts = {
    instances: {},

    cssVar: function (name, fallback) {
      try {
        var v = window.getComputedStyle(doc.documentElement).getPropertyValue(name);
        v = (v || '').trim();
        return v || fallback;
      } catch (e) { return fallback; }
    },

    hexToRgba: function (color, alpha) {
      var c = String(color || '').trim();
      if (c.charAt(0) === '#') {
        c = c.slice(1);
        if (c.length === 3) c = c.charAt(0) + c.charAt(0) + c.charAt(1) + c.charAt(1) + c.charAt(2) + c.charAt(2);
        if (c.length === 8) c = c.slice(0, 6);
        var num = parseInt(c, 16);
        if (!isNaN(num)) return 'rgba(' + ((num >> 16) & 255) + ',' + ((num >> 8) & 255) + ',' + (num & 255) + ',' + alpha + ')';
      }
      var m = c.match(/rgba?\(([^)]+)\)/);
      if (m) {
        var parts = m[1].split(',').map(function (p) { return p.trim(); });
        return 'rgba(' + parts[0] + ',' + (parts[1] || '0') + ',' + (parts[2] || '0') + ',' + alpha + ')';
      }
      return 'rgba(15,118,110,' + alpha + ')';
    },

    resolveCanvas: function (canvas) {
      return resolveElement(canvas);
    },

    /** يحضّر اللوحة مع مراعاة كثافة البكسل (DPR). */
    prepare: function (canvas, height) {
      var dpr = window.devicePixelRatio || 1;
      var rect = canvas.getBoundingClientRect();
      var width = Math.max(Math.round(rect.width || canvas.clientWidth || canvas.offsetWidth || 320), 40);
      var h = height || rect.height || canvas.clientHeight || 220;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.height = h + 'px';
      var ctx = canvas.getContext && canvas.getContext('2d');
      if (!ctx) return null;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, h);
      return { ctx: ctx, width: width, height: h };
    },

    palette: function () {
      return {
        primary: Charts.cssVar('--color-primary', '#0f766e'),
        success: Charts.cssVar('--color-success', '#16a34a'),
        danger: Charts.cssVar('--color-danger', '#dc2626'),
        warning: Charts.cssVar('--color-warning', '#d97706'),
        info: Charts.cssVar('--color-info', '#0284c7'),
        text: Charts.cssVar('--color-text-muted', '#64748b'),
        border: Charts.cssVar('--color-border', '#e2e8f0'),
        surface: Charts.cssVar('--color-surface', '#ffffff')
      };
    },

    /** حساب الأرقام العريضة المستخدمة في المحاور. */
    niceMax: function (value) {
      if (value <= 0) return 10;
      var exp = Math.floor(Math.log(value) / Math.LN10);
      var base = Math.pow(10, exp);
      var n = value / base;
      var step = n <= 1 ? 1 : (n <= 2 ? 2 : (n <= 5 ? 5 : 10));
      return step * base;
    },

    drawFrame: function (ctx, box, opts, maxValue) {
      var pal = opts.palette;
      var ticks = opts.ticks || 4;
      ctx.save();
      ctx.strokeStyle = pal.border;
      ctx.lineWidth = 1;
      ctx.fillStyle = pal.text;
      ctx.font = '11px "Cairo", system-ui, sans-serif';

      for (var i = 0; i <= ticks; i++) {
        var value = (maxValue / ticks) * i;
        var y = Math.round(box.bottom - (box.bottom - box.top) * (i / ticks)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(box.left, y);
        ctx.lineTo(box.right, y);
        ctx.stroke();
        ctx.textAlign = opts.mirror ? 'left' : 'right';
        ctx.textBaseline = 'middle';
        var label = opts.formatValue ? opts.formatValue(value) : Charts.shortNumber(value);
        if (opts.mirror) ctx.fillText(label, box.right + 8, y);
        else ctx.fillText(label, box.left - 8, y);
      }
      ctx.restore();
    },

    shortNumber: function (n) {
      var v = toNumber(n, 0);
      if (Math.abs(v) >= 1000000) return (v / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
      if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
      return String(Math.round(v * 100) / 100);
    },

    truncate: function (text, max) {
      var s = String(text === null || text === undefined ? '' : text);
      return (s.length > max) ? s.slice(0, max - 1) + '…' : s;
    },

    bar: function (canvas, labels, data, options) {
      guard('Charts.bar', function () {
        var el = Charts.resolveCanvas(canvas);
        if (!el) return;

        var opts = Object.assign({
          height: 240,
          mirror: isRTL(),
          ticks: 4,
          formatValue: null,
          showValues: true,
          color: null,
          palette: Charts.palette()
        }, options || {});

        var values = (data || []).map(function (d) { return toNumber(d, 0); });
        var names = (labels || []).map(function (l) { return Charts.truncate(l, 14); });
        var palette = opts.palette || Charts.palette();
        var color = opts.color || palette.primary;

        var env = Charts.prepare(el, opts.height);
        if (!env) return;
        var ctx = env.ctx, W = env.width, H = env.height;

        var padTop = 16, padBottom = 34;
        var padSide = opts.mirror ? 52 : 52;
        var box = {
          left: opts.mirror ? padSide : padSide,
          right: W - padSide,
          top: padTop,
          bottom: H - padBottom
        };
        if (box.right <= box.left) box.right = box.left + 10;

        var maxRaw = values.length ? Math.max.apply(null, values.concat([0])) : 0;
        var minRaw = values.length ? Math.min.apply(null, values.concat([0])) : 0;
        var maxValue = Charts.niceMax(maxRaw) || 10;
        var minValue = (minRaw >= 0) ? 0 : -Charts.niceMax(Math.abs(minRaw));

        Charts.drawFrame(ctx, box, opts, maxValue);

        var count = values.length || 1;
        var slot = (box.right - box.left) / count;
        var barW = Math.max(4, Math.min(slot * 0.62, 46));
        var zeroY = box.bottom - ((0 - minValue) / (maxValue - minValue)) * (box.bottom - box.top);

        ctx.save();
        for (var i = 0; i < values.length; i++) {
          var idx = opts.mirror ? (count - 1 - i) : i;
          var x = box.left + slot * idx + (slot - barW) / 2;
          var value = values[i];
          var ratio = (value - minValue) / (maxValue - minValue || 1);
          var y = box.bottom - ratio * (box.bottom - box.top);
          var h = Math.abs(zeroY - y);
          var barColor = Array.isArray(color) ? color[i % color.length] : color;

          var grad = ctx.createLinearGradient(0, Math.min(y, zeroY), 0, Math.max(y, zeroY));
          grad.addColorStop(0, Charts.hexToRgba(barColor, 0.95));
          grad.addColorStop(1, Charts.hexToRgba(barColor, 0.55));
          ctx.fillStyle = grad;

          var r = Math.min(6, barW / 2);
          var topY = value >= 0 ? y : zeroY;
          var height = Math.max(value >= 0 ? zeroY - y : y - zeroY, 2);
          if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, topY, barW, height, value >= 0 ? [r, r, 0, 0] : [0, 0, r, r]);
            ctx.fill();
          } else {
            ctx.fillRect(x, topY, barW, height);
          }

          /* اسم الفئة */
          ctx.fillStyle = palette.text;
          ctx.font = '11px "Cairo", system-ui, sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.direction = 'rtl';
          ctx.fillText(names[i] || '', x + barW / 2, box.bottom + 8);

          /* القيمة فوق العمود */
          if (opts.showValues) {
            ctx.fillStyle = palette.text;
            ctx.font = '600 11px "Cairo", system-ui, sans-serif';
            ctx.textBaseline = 'bottom';
            var vText = opts.formatValue ? opts.formatValue(value) : Charts.shortNumber(value);
            ctx.fillText(vText, x + barW / 2, Math.min(y, zeroY) - 4);
          }
        }
        ctx.restore();
        Charts.register(el, 'bar', names, values, opts);
      });
    },

    line: function (canvas, labels, data, options) {
      guard('Charts.line', function () {
        var el = Charts.resolveCanvas(canvas);
        if (!el) return;

        var opts = Object.assign({
          height: 240,
          mirror: isRTL(),
          ticks: 4,
          fill: true,
          showPoints: true,
          formatValue: null,
          color: null,
          palette: Charts.palette()
        }, options || {});

        var values = (data || []).map(function (d) { return toNumber(d, 0); });
        var names = (labels || []).map(function (l) { return Charts.truncate(l, 14); });
        var palette = opts.palette || Charts.palette();
        var color = opts.color || palette.primary;

        var env = Charts.prepare(el, opts.height);
        if (!env) return;
        var ctx = env.ctx, W = env.width, H = env.height;

        var padTop = 16, padBottom = 34, padSide = 52;
        var box = { left: padSide, right: W - padSide, top: padTop, bottom: H - padBottom };
        if (box.right <= box.left) box.right = box.left + 10;

        var maxRaw = values.length ? Math.max.apply(null, values) : 0;
        var minRaw = values.length ? Math.min.apply(null, values) : 0;
        var maxValue = Charts.niceMax(maxRaw) || 10;
        var minValue = minRaw - (maxValue - minRaw) * 0.15;
        if (minValue > 0) minValue = 0;

        Charts.drawFrame(ctx, box, opts, maxValue);

        var count = values.length;
        var step = count > 1 ? (box.right - box.left) / (count - 1) : 0;

        function px(i) {
          if (opts.mirror) return box.right - (count > 1 ? i * step : (box.right - box.left) / 2);
          return box.left + (count > 1 ? i * step : (box.right - box.left) / 2);
        }
        function py(v) {
          return box.bottom - ((v - minValue) / ((maxValue - minValue) || 1)) * (box.bottom - box.top);
        }

        ctx.save();
        ctx.direction = 'ltr';

        /* تعبئة المنطقة */
        if (opts.fill && count) {
          var area = ctx.createLinearGradient(0, box.top, 0, box.bottom);
          area.addColorStop(0, Charts.hexToRgba(color, 0.28));
          area.addColorStop(1, Charts.hexToRgba(color, 0.02));
          ctx.beginPath();
          ctx.moveTo(px(0), box.bottom);
          values.forEach(function (v, i) { ctx.lineTo(px(i), py(v)); });
          ctx.lineTo(px(count - 1), box.bottom);
          ctx.closePath();
          ctx.fillStyle = area;
          ctx.fill();
        }

        /* الخط */
        ctx.beginPath();
        values.forEach(function (v, i) {
          if (i === 0) ctx.moveTo(px(i), py(v));
          else ctx.lineTo(px(i), py(v));
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.stroke();

        /* النقاط */
        if (opts.showPoints) {
          values.forEach(function (v, i) {
            ctx.beginPath();
            ctx.arc(px(i), py(v), 3.5, 0, Math.PI * 2);
            ctx.fillStyle = palette.surface;
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.strokeStyle = color;
            ctx.stroke();
          });
        }

        /* تسميات المحور الأفقي */
        ctx.fillStyle = palette.text;
        ctx.font = '11px "Cairo", system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.direction = 'rtl';
        names.forEach(function (n, i) { ctx.fillText(n || '', px(i), box.bottom + 8); });
        ctx.restore();

        Charts.register(el, 'line', names, values, opts);
      });
    },

    /** يحفظ حالة الرسم لإعادة الرسم عند تغيّر الحجم. */
    register: function (el, type, labels, data, opts) {
      var key = String(el.id || Charts.instances._seq++ || (el.__mashiaId = Charts.instances._seq++));
      Charts.instances[key] = { el: el, type: type, labels: labels, data: data, opts: opts };

      if (el.__mashiaResize) return;
      var redraw = debounce(function () {
        var rec = Charts.instances[key];
        if (!rec) return;
        rec.opts = Object.assign({}, rec.opts, { palette: Charts.palette(), mirror: isRTL() });
        if (rec.type === 'bar') Charts.bar(rec.el, rec.labels, rec.data, rec.opts);
        else Charts.line(rec.el, rec.labels, rec.data, rec.opts);
      }, 160);

      if (typeof window.ResizeObserver === 'function') {
        var ro = new window.ResizeObserver(redraw);
        ro.observe(el);
        el.__mashiaResize = ro;
      } else {
        window.addEventListener('resize', redraw);
      }

      /* إعادة الرسم عند تبديل المظهر */
      doc.addEventListener('mashia:theme', function () {
        window.setTimeout(redraw, 60);
      });
    },

    /** يرسم كل الرسوم المعلَّمة [data-chart]. */
    renderAll: function () {
      guard('Charts.renderAll', function () {
        qsa('canvas[data-chart]').forEach(function (el) {
          var type = el.getAttribute('data-chart') || 'bar';
          var labels = [];
          var data = [];
          try {
            labels = JSON.parse(el.getAttribute('data-labels') || '[]');
            data = JSON.parse(el.getAttribute('data-values') || '[]');
          } catch (e) { return; }
          var opts = {};
          try {
            if (el.getAttribute('data-options')) opts = JSON.parse(el.getAttribute('data-options'));
          } catch (e2) { opts = {}; }
          if (type === 'line') Charts.line(el, labels, data, opts);
          else Charts.bar(el, labels, data, opts);
        });
      });
    },

    init: function () {
      guard('Charts.init', function () {
        if (!qs('canvas[data-chart]')) return;
        onReady(function () { Charts.renderAll(); });
      });
    }
  };
  Charts.instances._seq = 1;
  MashiaApp.Charts = Charts;

  /* ==========================================================================
   * 11) Print — الطباعة
   * ========================================================================== */
  var Print = {
    root: null,

    /** يطبع عنصراً بعينه (نسخة مؤقتة داخل .print-root). */
    element: function (target) {
      return guard('Print.element', function () {
        var el = resolveElement(target);
        if (!el) { Toasts.show('العنصر المطلوب طباعته غير موجود.', 'danger'); return false; }

        var root = Print.root;
        if (!root) {
          root = doc.createElement('div');
          root.className = 'print-root';
          root.setAttribute('aria-hidden', 'true');
          doc.body.appendChild(root);
          Print.root = root;
        }
        root.innerHTML = '';

        var clone = el.cloneNode(true);
        clone.removeAttribute('id');
        clone.classList.remove('print-root');
        Array.prototype.slice.call(clone.querySelectorAll('[id]')).forEach(function (n) { n.removeAttribute('id'); });
        root.appendChild(clone);

        doc.body.classList.add('printing');
        window.setTimeout(function () {
          window.print();
          window.setTimeout(Print.cleanup, 400);
        }, 60);

        if (typeof window.addEventListener === 'function') {
          window.addEventListener('afterprint', Print.cleanup, { once: true });
        }
        return true;
      });
    },

    cleanup: function () {
      guard('Print.cleanup', function () {
        doc.body.classList.remove('printing');
        if (Print.root) Print.root.innerHTML = '';
      });
    },

    init: function () {
      guard('Print.init', function () {
        doc.addEventListener('click', function (e) {
          var btn = e.target && e.target.closest ? e.target.closest('[data-print]') : null;
          if (!btn) return;
          e.preventDefault();
          var value = (btn.getAttribute('data-print') || '').trim();
          if (value && value !== 'true' && value !== 'window') Print.element(value);
          else window.print();
        });

        window.addEventListener('afterprint', Print.cleanup);
      });
    }
  };
  MashiaApp.Print = Print;

  /* ==========================================================================
   * 12) Tables — البحث والترتيب داخل الجداول
   * ========================================================================== */
  var Tables = {
    normalize: function (str) {
      return String(str === null || str === undefined ? '' : str)
        .replace(/[\u064B-\u065F\u0670]/g, '')   /* إزالة التشكيل */
        .replace(/[أإآٱ]/g, 'ا')
        .replace(/ى/g, 'ي')
        .replace(/ة/g, 'ه')
        .replace(/\s+/g, ' ')
        .trim()
        .toLowerCase();
    },

    /** تصفية صفوف الجدول حسب نص البحث. */
    filter: function (input, table) {
      if (!input || !table) return 0;
      var term = Tables.normalize(input.value);
      var body = table.tBodies[0];
      if (!body) return 0;

      var rows = Array.prototype.slice.call(body.rows);
      var visible = 0;

      rows.forEach(function (row) {
        if (row.hasAttribute('data-always-visible')) { row.hidden = false; visible++; return; }
        var text = Tables.normalize(row.textContent);
        var match = (term === '') || (text.indexOf(term) !== -1);
        row.hidden = !match;
        if (match) visible++;
      });

      var counter = table.parentElement && table.parentElement.querySelector('[data-filter-count]');
      if (counter) counter.textContent = String(visible);

      var empty = table.parentElement && table.parentElement.querySelector('[data-filter-empty]');
      if (empty) empty.classList.toggle('d-none', visible !== 0);

      var wrapper = table.parentElement && table.parentElement.querySelector('.table-wrapper');
      if (wrapper && term === '') wrapper.scrollLeft = 0;

      return visible;
    },

    /** ترتيب الجدول حسب العمود. */
    sortBy: function (table, header) {
      if (!table || !header) return;
      var body = table.tBodies[0];
      if (!body) return;

      var allHeaders = qsa('[data-sort]', table);
      var index = allHeaders.indexOf(header);
      if (index === -1) return;

      var current = header.getAttribute('aria-sort');
      var asc = (current !== 'ascending');
      var type = header.getAttribute('data-sort-type') ||
        (header.querySelector('[data-sort-type]') || {}).getAttribute && header.querySelector('[data-sort-type]').getAttribute('data-sort-type') || 'auto';

      allHeaders.forEach(function (h) {
        h.classList.remove('sort-asc', 'sort-desc');
        h.setAttribute('aria-sort', 'none');
      });
      header.classList.add(asc ? 'sort-asc' : 'sort-desc');
      header.setAttribute('aria-sort', asc ? 'ascending' : 'descending');

      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var ca = a.cells[index];
        var cb = b.cells[index];
        if (!ca || !cb) return 0;
        var va = ca.getAttribute('data-sort-value') !== null ? ca.getAttribute('data-sort-value') : ca.textContent;
        var vb = cb.getAttribute('data-sort-value') !== null ? cb.getAttribute('data-sort-value') : cb.textContent;
        var result;
        if (type === 'number') {
          result = toNumber(va, 0) - toNumber(vb, 0);
        } else if (type === 'date') {
          result = (Date.parse(va) || 0) - (Date.parse(vb) || 0);
        } else if (type === 'auto') {
          var na = normalizeNumber(va);
          var nb = normalizeNumber(vb);
          var aIsNum = !isNaN(na) && /\d/.test(String(va));
          var bIsNum = !isNaN(nb) && /\d/.test(String(vb));
          if (aIsNum && bIsNum) result = na - nb;
          else result = Tables.normalize(va).localeCompare(Tables.normalize(vb), MashiaApp.config.locale);
        } else {
          result = String(va).localeCompare(String(vb), MashiaApp.config.locale);
        }
        return asc ? result : -result;
      });

      rows.forEach(function (row) { body.appendChild(row); });
    },

    /** تحديد كل الصفوف عبر مربع الاختيار الرئيسي. */
    bindSelectAll: function (master) {
      if (!master) return;
      var table = master.closest('table');
      if (!table) return;
      master.addEventListener('change', function () {
        qsa('tbody .form-check-input[data-row-select]', table).forEach(function (cb) {
          cb.checked = master.checked;
          var row = cb.closest('tr');
          if (row) row.classList.toggle('table-row-selected', master.checked);
        });
      });
    },

    init: function () {
      guard('Tables.init', function () {
        /* البحث */
        qsa('[data-filter-input]').forEach(function (input) {
          var targets = input.getAttribute('data-filter-target');
          var tables = [];
          if (targets) {
            tables = targets.split(',').map(function (t) {
              var sel = t.trim();
              return sel.charAt(0) === '#' || sel.charAt(0) === '.' || sel.indexOf('[') === 0 ? qs(sel) : null;
            }).filter(Boolean);
          } else {
            var parentTable = input.closest('.table-wrapper') || input.parentElement;
            tables = qsa('table', parentTable || doc);
          }

          if (!tables.length) return;

          var run = debounce(function () {
            tables.forEach(function (table) { Tables.filter(input, table); });
          }, 180);

          input.addEventListener('input', run);
          input.addEventListener('search', run);

          /* زر مسح البحث */
          var clear = input.getAttribute('data-filter-clear-target');
          if (clear) {
            var btn = qs(clear);
            if (btn) {
              btn.addEventListener('click', function () {
                input.value = '';
                run();
                input.focus();
              });
            }
          }
        });

        /* الترتيب */
        qsa('[data-sort]').forEach(function (header) {
          header.setAttribute('tabindex', header.getAttribute('tabindex') || '0');
          header.setAttribute('role', 'columnheader');
          header.setAttribute('aria-sort', 'none');
          var table = header.closest('table');
          function doSort() { Tables.sortBy(table, header); }
          header.addEventListener('click', doSort);
          header.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doSort(); }
          });
        });

        /* تحديد الكل */
        qsa('[data-select-all]').forEach(function (cb) { Tables.bindSelectAll(cb); });

        /* ترقيم الصفحات في الواجهة */
        qsa('[data-page-size]').forEach(function (select) {
          select.addEventListener('change', function () {
            var table = qs(select.getAttribute('data-page-size'));
            if (!table || !table.tBodies[0]) return;
            var size = parseInt(select.value, 10) || 20;
            Array.prototype.slice.call(table.tBodies[0].rows).forEach(function (row, i) {
              row.hidden = i >= size;
            });
          });
        });
      });
    }
  };
  MashiaApp.Tables = Tables;

  /* ==========================================================================
   * 13) PWA — تسجيل Service Worker
   * ========================================================================== */
  var PWA = {
    registration: null,
    waitingWorker: null,

    supported: function () {
      if (!('serviceWorker' in navigator)) return false;
      var loc = window.location;
      if (loc.protocol === 'https:') return true;
      var host = (loc.hostname || '').toLowerCase();
      return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
    },

    /** يطلب من العامل الجديد التفعيل ثم يعيد تحميل الصفحة. */
    update: function (reload) {
      guard('PWA.update', function () {
        if (!PWA.waitingWorker) {
          if (reload !== false) window.location.reload();
          return;
        }
        PWA.waitingWorker.postMessage({ type: 'SKIP_WAITING' });
        if (reload !== false) window.location.reload();
      });
    },

    notifyUpdate: function (worker) {
      guard('PWA.notifyUpdate', function () {
        PWA.waitingWorker = worker;
        var btn = qs('[data-sw-update]');
        if (btn) btn.classList.remove('d-none');
        Toasts.show('يتوفّر تحديث جديد للمنصة.', 'info', {
          title: 'تحديث متاح',
          timeout: 0,
          actionLabel: 'تحديث الآن',
          actionUrl: '#',
          onClose: function () { PWA.update(); }
        });
      });
    },

    init: function () {
      guard('PWA.init', function () {
        if (!PWA.supported()) return;

        window.addEventListener('load', function () {
          navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function (reg) {
            PWA.registration = reg;

            if (reg.waiting && navigator.serviceWorker.controller) PWA.notifyUpdate(reg.waiting);

            reg.addEventListener('updatefound', function () {
              var newWorker = reg.installing;
              if (!newWorker) return;
              newWorker.addEventListener('statechange', function () {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  PWA.notifyUpdate(newWorker);
                }
              });
            });

            /* فحص دوري للتحديثات */
            window.setInterval(function () { reg.update().catch(function () { /* ignore */ }); }, 60 * 60 * 1000);
          }).catch(function (err) { debug('PWA.register', err); });
        });

        var reloading = false;
        navigator.serviceWorker.addEventListener('controllerchange', function () {
          if (reloading) return;
          reloading = true;
        });

        qsa('[data-sw-update]').forEach(function (btn) {
          btn.addEventListener('click', function (e) { e.preventDefault(); PWA.update(); });
        });
      });
    }
  };
  MashiaApp.PWA = PWA;

  /* ==========================================================================
   * 14) Money — تنسيق الأرقام والمبالغ
   * ========================================================================== */
  var Money = {
    formatterCache: {},

    buildFormatter: function (locale, opts) {
      var key = locale + '|' + JSON.stringify(opts);
      if (Money.formatterCache[key]) return Money.formatterCache[key];
      try {
        var nf = new Intl.NumberFormat(locale, opts);
        Money.formatterCache[key] = nf;
        return nf;
      } catch (e) {
        return null;
      }
    },

    /** تنسيق رقمي بفواصل الآلاف. */
    format: function (value, options) {
      return guard('Money.format', function () {
        var opts = options || {};
        var n = normalizeNumber(value);
        if (isNaN(n)) return opts.fallback || '0';

        var decimals = (opts.decimals === undefined || opts.decimals === null) ? 0 : opts.decimals;
        var intOpts = {
          minimumFractionDigits: (opts.minimumFractionDigits === undefined) ? decimals : opts.minimumFractionDigits,
          maximumFractionDigits: (opts.maximumFractionDigits === undefined) ? decimals : opts.maximumFractionDigits,
          useGrouping: opts.useGrouping !== false
        };

        var nf = Money.buildFormatter(opts.locale || MashiaApp.config.locale, intOpts);
        if (nf) return nf.format(n);

        /* بديل يدوي عند غياب Intl */
        var fixed = Math.abs(n).toFixed(intOpts.maximumFractionDigits);
        var parts = fixed.split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ') || '0';
        var out = parts.join(',');
        return (n < 0 ? '-' : (opts.signDisplay === 'always' ? '+' : '')) + out;
      });
    },

    /** تنسيق مبلغ مالي مع العملة (مثل: 12 500 د.ج). */
    currency: function (value, code, options) {
      return guard('Money.currency', function () {
        var opts = Object.assign({}, options || {});
        var n = normalizeNumber(value);
        if (isNaN(n)) return opts.fallback || '0 ' + (opts.symbol || MashiaApp.config.currencySymbol);

        var locale = opts.locale || MashiaApp.config.locale;
        try {
          return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: code || MashiaApp.config.currency,
            minimumFractionDigits: (opts.decimals === undefined) ? 0 : opts.decimals,
            maximumFractionDigits: (opts.decimals === undefined) ? 2 : opts.decimals
          }).format(n);
        } catch (e) {
          return Money.format(n, opts) + ' ' + (opts.symbol || MashiaApp.config.currencySymbol);
        }
      });
    },

    /** نسبة مئوية. */
    percent: function (value, options) {
      var opts = options || {};
      var n = normalizeNumber(value);
      if (isNaN(n)) return opts.fallback || '0%';
      var decimals = (opts.decimals === undefined) ? 0 : opts.decimals;
      return Money.format(n, { decimals: decimals }) + '%';
    },

    /** يحدّث كل عناصر [data-money] من قيمة data-value. */
    applyAll: function (root) {
      guard('Money.applyAll', function () {
        qsa('[data-money]', root || doc).forEach(function (el) {
          var raw = el.getAttribute('data-value');
          if (raw === null || raw === '') raw = el.textContent;
          var kind = el.getAttribute('data-money') || 'number';
          var text;
          if (kind === 'currency') text = Money.currency(raw, el.getAttribute('data-currency'));
          else if (kind === 'percent') text = Money.percent(raw);
          else text = Money.format(raw, { decimals: parseInt(el.getAttribute('data-decimals'), 10) || 0 });
          el.textContent = text;
        });
      });
    },

    init: function () {
      guard('Money.init', function () {
        if (qs('[data-money]')) Money.applyAll();
      });
    }
  };
  MashiaApp.Money = Money;

  /* ==========================================================================
   * 15) Dropdowns و Tabs — سلوكيات الواجهة
   * ========================================================================== */
  var Dropdowns = {
    closeAll: function (except) {
      qsa('.dropdown.open, .dropdown.show').forEach(function (dd) {
        if (dd === except) return;
        dd.classList.remove('open', 'show');
        var toggle = dd.querySelector('.dropdown-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
      });
    },

    toggle: function (dropdown) {
      if (!dropdown) return;
      var isOpen = dropdown.classList.contains('open') || dropdown.classList.contains('show');
      Dropdowns.closeAll(dropdown);
      dropdown.classList.toggle('open', !isOpen);
      dropdown.classList.toggle('show', !isOpen);
      var btn = dropdown.querySelector('.dropdown-toggle');
      if (btn) btn.setAttribute('aria-expanded', String(!isOpen));
      if (!isOpen) {
        var first = dropdown.querySelector('.dropdown-item');
        if (first) { try { first.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
      }
    },

    init: function () {
      guard('Dropdowns.init', function () {
        doc.addEventListener('click', function (e) {
          var toggle = e.target && e.target.closest ? e.target.closest('.dropdown-toggle') : null;
          if (toggle) {
            var dd = toggle.closest('.dropdown');
            if (dd) { e.preventDefault(); Dropdowns.toggle(dd); }
            return;
          }
          if (!e.target.closest || !e.target.closest('.dropdown-menu')) Dropdowns.closeAll(null);
        });

        doc.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') Dropdowns.closeAll(null);
        });
      });
    }
  };
  MashiaApp.Dropdowns = Dropdowns;

  var Tabs = {
    activate: function (tab) {
      if (!tab) return;
      var group = tab.closest('[role="tablist"], .tabs') || tab.parentElement;
      if (!group) return;
      var targetId = tab.getAttribute('data-tab-target') || tab.getAttribute('href') || tab.getAttribute('aria-controls');

      qsa('[role="tab"], .tab', group).forEach(function (t) {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
        t.setAttribute('tabindex', '-1');
      });
      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');
      tab.setAttribute('tabindex', '0');

      if (targetId && targetId.charAt(0) === '#') {
        var pane = qs(targetId);
        if (pane) {
          var panes = pane.parentElement.querySelectorAll('.tab-pane');
          Array.prototype.slice.call(panes).forEach(function (p) { p.classList.remove('active', 'show'); });
          pane.classList.add('active', 'show');
        }
      }
    },

    init: function () {
      guard('Tabs.init', function () {
        qsa('[role="tab"], .tab').forEach(function (tab) {
          if (!tab.hasAttribute('role')) tab.setAttribute('role', 'tab');
          tab.setAttribute('tabindex', tab.classList.contains('active') ? '0' : '-1');
          tab.addEventListener('click', function (e) {
            e.preventDefault();
            Tabs.activate(tab);
          });
        });
      });
    }
  };
  MashiaApp.Tabs = Tabs;

  /* ==========================================================================
   * أدوات مصدَّرة
   * ========================================================================== */
  MashiaApp.qs = qs;
  MashiaApp.qsa = qsa;
  MashiaApp.resolveElement = resolveElement;
  MashiaApp.onReady = onReady;
  MashiaApp.debounce = debounce;
  MashiaApp.escapeHtml = escapeHtml;
  MashiaApp.toNumber = toNumber;
  MashiaApp.normalizeNumber = normalizeNumber;

  /* ==========================================================================
   * الإقلاع (Bootstrap)
   * ========================================================================== */
  Theme.init();          /* مبكراً: يمنع وميض الألوان */

  var MODULES = ['Modals', 'Toasts', 'Forms', 'Confirm', 'Sidebar', 'Attendance',
    'Notifications', 'Filters', 'Charts', 'Print', 'Tables', 'PWA', 'Money',
    'Dropdowns', 'Tabs'];

  MashiaApp.init = function () {
    onReady(function () {
      MODULES.forEach(function (name) {
        var mod = MashiaApp[name];
        if (mod && typeof mod.init === 'function') guard(name + '.init', mod.init);
      });
    });
  };

  MashiaApp.init();

})(window, document);
