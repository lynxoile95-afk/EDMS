# EDMS-AI — سامانه مدیریت مدارک مهندسی هوشمند (فاز ۱)

> از PDF نقشه تا برگه ارسال (Transmittal): استخراج کادر مشخصات، تطبیق با فهرست مدارک پروژه (MDL)، تصمیم‌گیری قاعده‌محور و صدور برگه ارسال — همه به‌صورت درون‌سازمانی (On-Premise).

---

## ۱. این سیستم چه کاری انجام می‌دهد؟

مسیر اصلی و «کارِ واقعی» که این نسخه انجام می‌دهد:

```
بارگذاری PDF  →  استخراج کادر مشخصات (لایه متن → OCR → VLM اختیاری)
              →  نرمال‌سازی کد/بازنگری/عنوان
              →  تطبیق با MDL (دقیق / نام مستعار / واریانت OCR / فازی)
              →  موتور تصمیم قاعده‌محور (R01…R22)  →  GREEN / YELLOW / RED
              →  بازبینی انسانی (DCC) و ثبت بازنگری
              →  گروه‌بندی گیرندگان از DDM  →  پیش‌نویس برگه ارسال
              →  صدور نهایی + شماره + PDF فارسی + اعلان و کارتابل
              →  تأییدیه دریافت (Acknowledge) و فهرست نظرات (Comment List)
```

اصل طراحی: **هوش مصنوعی پیشنهاد می‌دهد، قاعده تصمیم می‌گیرد.** هیچ مدلی به‌تنهایی مدرک را «ثبت» نمی‌کند؛ هر فیلد با درصد اطمینان، منبع استخراج، متن خام، مرجع برش تصویر و نسخه مدل ذخیره می‌شود تا قابل ممیزی باشد.

---

## ۲. پیش‌فرض‌ها و تصمیم‌های طراحی (Assumptions)

| # | فرض / تصمیم | دلیل |
|---|---|---|
| A1 | فاز ۱ فقط PDF (نقشه و مدرک) را پردازش می‌کند؛ DWG/Revit در فاز بعد. | دامنه درخواست و ابزارهای موجود (PyMuPDF/pdfplumber). |
| A2 | ساختار کد مدرک در هر پروژه قابل تنظیم است (`doc_code_pattern`)؛ پیش‌فرض `<PRJ>-<DISC>-<TYPE>-<SEQ>` + یونیت و شیت. | کدگذاری شرکت‌ها متفاوت است. |
| A3 | مبنای تصمیم «مدل زبانی بزرگ» نیست؛ موتور قاعده با وزن‌دهی فیلدها است. | الزام ممیزی، قابل توضیح بودن، هزینه و امنیت. |
| A4 | VLM فقط برای موارد مبهم و فقط مدل محلی سازگار با OpenAI (vLLM/Ollama). پیش‌فرض خاموش. | محرمانگی اسناد؛ «هیچ AI ابری». |
| A5 | احراز هویت با JWT است و پشت یک لایه Provider (local / LDAP / OIDC) قرار می‌گیرد. | الزام «پشت LDAP/AD/Keycloak». |
| A6 | زمان همه‌جا UTC ذخیره می‌شود؛ تبدیل به تاریخ شمسی فقط در نمایش. | جلوگیری از خطای منطقه زمانی. |
| A7 | «ثبت بازنگری + ساخت برگه ارسال» در یک تراکنش دیتابیس انجام می‌شود؛ در صورت خطا هیچ‌چیز ثبت نمی‌شود (Atomic). | جلوگیری از مدارک بدون ارسال. |
| A8 | شماره برگه ارسال از `NumberSequence` با قفل ردیف و بدون استفاده مجدد تخصیص می‌یابد. | یکتایی و ممیزی. |
| A9 | برگه ارسال خارجی نیازمند تأیید مدیر پروژه است و در وضعیت `pending_approval` متوقف می‌شود. | کنترل ارتباطات بیرونی. |
| A10 | کارتابل و ارسال ایمیل/SMS «Console» به‌صورت پیش‌فرض محلی است؛ SMTP قابل تنظیم. | اجرای بدون وابستگی بیرونی. |
| A11 | رابط کاربری فارسی و RTL است؛ برچسب‌های دوزبانه در سطح داده (کد + عنوان انگلیسی) نگه‌داری می‌شود. | الزام کارفرما. |
| A12 | SQLite فقط برای تست است؛ محیط عملیاتی PostgreSQL 16 با JSONB. | فیلدهای پویا و ایندکس GIN. |

---

## ۳. معماری (خلاصه)

```
                ┌────────────────────────────┐
   مرورگر ────► │ nginx (:8080)              │
                │  /        → SPA (React)    │
                │  /api     → FastAPI :8000  │
                └──────────────┬─────────────┘
                               │
                    ┌──────────▼───────────┐
                    │ FastAPI (api)        │
                    │ JWT · RBAC · ACL     │
                    └───┬────────┬─────────┘
                        │        │
        ┌───────────────▼──┐  ┌──▼────────────────┐
        │ PostgreSQL 16    │  │ MinIO (S3)        │
        │ JSONB + ممیزی    │  │ PDF/برش/PDF خروجی │
        └──────────────────┘  └───────────────────┘
                        │
                    ┌───▼────────────┐        ┌──────────────┐
                    │ Redis (broker) │◄──────►│ Celery worker│
                    └────────────────┘        │ ingest       │
                                              │ extract      │
                                              │ transmittal  │
                                              │ notify       │
                                              └──────┬───────┘
                                                     │
                              ┌──────────────────────▼─────────────────┐
                              │ استخراج: pdfplumber → PyMuPDF → OCR     │
                              │ (Tesseract fas+eng) → VLM (اختیاری)     │
                              └────────────────────────────────────────┘
```

جزئیات کامل: [`docs/02-architecture.md`](docs/02-architecture.md) — دامنه و الزامات: [`docs/01-scope-and-requirements.md`](docs/01-scope-and-requirements.md)

### لایه‌های استخراج (ارزان‌ترین اول)

1. **لایه متن/وکتور** — `pdfplumber` در ناحیه کادر مشخصات (پایین/گوشه صفحه) و `PyMuPDF` برای fallback و استخراج bbox.
2. **OCR** — اگر لایه متن وجود نداشت یا نامعتبر بود: رندر ۳۰۰dpi + Tesseract (`fas+eng`)، موتور OCR قابل تعویض با PaddleOCR از طریق `OCR_ENGINE`.
3. **VLM محلی (اختیاری)** — فقط اگر دو مرحله قبل زیر آستانه اطمینان باشند و `VLM_ENABLED=true` (سقف `VLM_MAX_CALLS_PER_DOCUMENT`).

هر سه لایه خروجی یکسان (`TitleBlockResult`) می‌دهند: فیلدها + اطمینان + bbox + منبع + متن خام.

---

## ۴. راه‌اندازی سریع (Docker Compose)

```bash
cp .env.example .env
# حداقل SECRET_KEY و رمزهای دیتابیس/مینیو را عوض کنید
python -c "import secrets;print(secrets.token_urlsafe(48))"   # مقدار SECRET_KEY

docker compose up -d --build
docker compose run --rm api python -m app.bootstrap            # ساخت مدیر و داده نمونه
```

| سرویس | آدرس |
|---|---|
| رابط کاربری (از طریق nginx) | http://localhost:8080 |
| مستندات OpenAPI | http://localhost:8000/api/docs |
| MinIO Console | http://localhost:9001 |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |

ورود با `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` از فایل `.env`.

---

## ۵. اجرای محلی بدون Docker

### بک‌اند

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend
export DATABASE_URL="postgresql+psycopg://edms:edms@localhost:5432/edms"
export SECRET_KEY="dev-secret"
alembic upgrade head
python -m app.bootstrap
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

بدون PostgreSQL هم می‌توان توسعه داد (SQLite + ذخیره‌سازی محلی):

```bash
cd backend
export DATABASE_URL="sqlite:////tmp/edms.db" STORAGE_BACKEND=local LOCAL_STORAGE_PATH=/tmp/edms-storage
export CELERY_TASK_ALWAYS_EAGER=true     # پردازش در همان پروسه، بدون Redis
python -m app.bootstrap && uvicorn app.main:app --port 8000
```

### worker

```bash
celery -A app.workers.celery_app.celery_app worker -l info -Q ingest,extract,transmittal,notify
```

### فرانت‌اند

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173  (پروکسی /api → VITE_PROXY_TARGET)
npm run build        # خروجی تولید در dist/
```

---

## ۶. تست‌ها

```bash
cd backend
../.venv/bin/python -m pytest -q
```

**نتیجه فعلی: ۱۴۹ تست، همه سبز (149 passed).** محیط تست کاملاً مستقل است: SQLite موقت + ذخیره‌سازی محلی + `CELERY_TASK_ALWAYS_EAGER=true`.

| فایل | چه چیزی را تضمین می‌کند |
|---|---|
| `test_normalization.py` | نرمال‌سازی کد/بازنگری/عنوان، ارقام فارسی/عربی، علائم ویرایشی، `REV_PREFIX_RE` و سخت‌گیری روی `R` تنها |
| `test_code_matching.py` | تطبیق دقیق/نام مستعار/واریانت OCR/فازی، آستانه `ACCEPT_SCORE`، انتخاب بهترین کاندید |
| `test_revisions.py` | ترتیب بازنگری‌ها، تشخیص تکراری/قدیمی‌تر/پرش، مقایسه با بازنگری جاری MDL |
| `test_decision_engine.py` | خروجی GREEN/YELLOW/RED و کدهای قاعده در سه سناریوی مرجع |
| `test_title_block.py` | استخراج از متن ساختگی، تشخیص الگوی کد، یافتن کادر مشخصات در صفحه |
| `test_transmittal_grouping.py` | گروه‌بندی گیرندگان از DDM، کلید یکتایی (idempotency)، تأییدیه، خطاهای صدور |
| `test_mdl_import.py` | تشخیص سرستون فارسی، پیش‌نمایش، ایمپورت اتمیک، ردیف‌های تکراری/نامعتبر، خروجی اکسل |
| `test_api_flow.py` | جریان کامل API: ورود، بارگذاری، تصمیم‌گیری، ثبت، صدور، کارتابل |
| `test_processing_session.py` | اجرای واقعی worker روی یک PDF ساخته‌شده در تست: استخراج، تطبیق، تصمیم، ثبت نتیجه؛ رفتار فایل خراب؛ و مسیر دانلود محلی |

---

## ۷. موتور تصمیم و قواعد (R01–R22)

امتیاز نهایی از ترکیب وزن‌دار فیلدها ساخته می‌شود:

| فیلد | وزن |
|---|---|
| کد مدرک | ۰٫۴۰ |
| بازنگری | ۰٫۲۵ |
| عنوان | ۰٫۱۵ |
| دیسیپلین | ۰٫۱۰ |
| وضعیت مدرک | ۰٫۰۵ |
| یونیت | ۰٫۰۵ |

آستانه‌های پیش‌فرض پروژه: `green=0.85`، `yellow_min=0.45`، `field_min=0.60`، `code_similarity=0.80`، `title_similarity=0.62`.

| کد | عنوان |
|---|---|
| R01 | کد مدرک یافت نشد |
| R02 | کد مدرک با الگوی پروژه سازگار نیست |
| R03 | کد مدرک در MDL موجود نیست |
| R04 | تطبیق نزدیک با MDL |
| R05 | شماره بازنگری نامعتبر |
| R06 | بازنگری تکراری |
| R07 | بازنگری قدیمی‌تر از جاری |
| R08 | پرش در بازنگری |
| R09 | اختلاف عنوان با MDL |
| R10 | اختلاف دیسیپلین |
| R11 | اختلاف یونیت |
| R12 | وضعیت مدرک اجازه ارسال نمی‌دهد |
| R13 | نام فایل با کد نقشه نمی‌خواند |
| R14 | فایل تکراری |
| R15 | اطمینان پایین استخراج |
| R16 | استفاده از OCR |
| R17 | استفاده از مدل بینایی |
| R18 | کد تکراری در یک جلسه |
| R19 | اختلاف تعداد شیت با MDL |
| R20 | کادر مشخصات یافت نشد |
| R22 | خطا در استخراج |

(کد R21 در فاز ۱ استفاده نشده و برای قاعده «تغییر ماهیت مدرک» در فاز ۲ رزرو است.)

`severity` هر قاعده `info` / `warning` / `error` است؛ ایرادهای `error` تصمیم را حداکثر به `YELLOW` کاهش می‌دهند و ثبت نهایی را نیازمند تأیید صریح (`override_issues`) می‌کنند.

---

## ۸. جریان برگه ارسال

* **گروه‌بندی:** پیش‌فرض `by_recipients` — گیرندگان از قواعد DDM (دیسیپلین، یونیت، نوع مدرک، هدف) استخراج می‌شوند؛ نزدیک‌ترین/خاص‌ترین قاعده برنده است و در تساوی، `priority` بالاتر.
* **یکتایی (Idempotency):** `sha256(project | purpose | sorted(recipient_ids) | sorted(revision_ids))` — ساخت مجدد پیش‌نویس به همان برگه برمی‌گردد.
* **شماره‌گذاری:** قالب از تنظیمات پروژه، پیش‌فرض `TRN-<CODE>-<seq:03d>` (قالب داخلی `T-{project}-{seq:03d}`)، تخصیص با قفل ردیف و بدون استفاده مجدد.
* **صدور:** مدارک خطوط → `issued`، شماره نهایی، فهرست نظرات باز، اعلان به گیرندگان، مهلت پاسخ بر اساس تقویم کاری (پنجشنبه/جمعه تعطیل). صدور دوباره یا صدور بدون گیرنده مجاز نیست.
* **تأییدیه:** فقط گیرنده می‌تواند Acknowledge کند؛ وضعیت برگه به `partially_acknowledged` / `acknowledged` می‌رود.
* **PDF:** قالب فارسی RTL با WeasyPrint (و در نبود آن ReportLab) از روی داده دیتابیس — نه فایل ذخیره‌شده.

### ۸-۱. گردش بازبینی (Review Route)

برگه ارسال پس از صدور از چهار دست عبور می‌کند. هر گذر یک ردیف در جدول
`transmittal_referrals` می‌سازد، پس تاریخ گردش از خود داده‌ها بازسازی می‌شود، نه از حافظه.

```text
پیمانکار            کارشناس کنترل اسناد       مدیر مهندسی / مدیر پروژه     کارشناس دیسیپلین
submitter  ──صدور──▶ dcc  ────forward────▶  management  ────refer────▶  discipline
                                          ◀────complete (بازگشت به مدیریت)────
```

| مرحله (`stage`) | معنی | چه کسی اقدام می‌کند |
|---|---|---|
| `draft` | پیش‌نویس، هنوز صادر نشده | پیمانکار / کنترل اسناد |
| `dcc` | صادر شده و روی میز کنترل اسناد است | `dcc` → `POST /transmittals/{id}/forward` |
| `management` | در دست بررسی مدیریت | `engineering_manager` یا `project_manager` → `POST /transmittals/{id}/refer` |
| `discipline` | ارجاع‌شده به کارشناس یک دیسیپلین | گیرنده ارجاع → `POST /transmittals/{id}/referrals/{rid}/complete` (برمی‌گردد به `management`) |
| `closed` | گردش تمام شده | — |

قواعد سخت‌گیرانه:

* `forward` فقط از `dcc` (یا `project_manager`) پذیرفته می‌شود؛ `refer` فقط از مدیریت.
* `refer` یا `discipline_id` می‌خواهد یا `to_user_id`. اگر دیسیپلین انتخاب شود و هیچ کارشناسی
  به آن تخصیص نیافته باشد، پاسخ `422` با پیام «برای دیسیپلین … کارشناس تعریف نشده است» است.
* هر ارجاع تا پاسخ گیرنده `pending` است و برگه در کارتابل او می‌ماند
  (`GET /projects/{id}/transmittals?mine=true`).
* کارشناس یک دیسیپلین، برگه‌های دیسیپلین دیگر را در کارتابل خود نمی‌بیند.

### ۸-۳. MDL: چه کسی می‌نویسد، چه کسی می‌خواند

MDL سند رسمیِ پروژه است و فقط **کارشناس کنترل اسناد مهندسی** آن را می‌نویسد.
`system_admin` (ابرمستخدم) استثناست، چون در مدل دسترسی ما همیشه از ACL عبور می‌کند.

| عملیات | مسیر | مجاز |
|---|---|---|
| پیش‌نمایش اکسل و نگاشت ستون‌ها | `POST /projects/{id}/mdl/preview` | فقط `dcc` |
| ایمپورت اکسل | `POST /projects/{id}/mdl/import` | فقط `dcc` |
| افزودن ردیف دستی | `POST /projects/{id}/mdl/items` | فقط `dcc` |
| ویرایش ردیف | `PATCH /projects/{id}/mdl/items/{item_id}` | فقط `dcc` |
| حذف ردیف | `DELETE /projects/{id}/mdl/items/{item_id}` | فقط `dcc`؛ اگر ردیف بازنگری ثبت‌شده داشته باشد `409` |
| مشاهده، جست‌وجو، خروجی اکسل | `GET …/mdl/items` · `…/mdl/export` | همهٔ اعضای پروژه |

قواعدی که هر دو درِ ورود (اکسل و دستی) را می‌بندد:

* کد مدرک در پروژه یکتاست؛ ردیف تکراری `409` می‌گیرد.
* کد دیسیپلین/یونیت باید از‌قبل در پروژه تعریف شده باشد؛ در غیر این صورت `422`.
* ردیف دستی دقیقاً مثل ردیف ایمپورت‌شده در تطبیق کد مدرک شرکت می‌کند (`doc_code_norm`).

در UI، دکمه‌های «بارگذاری MDL (اکسل)»، «افزودن ردیف دستی» و ستون «عملیات» (ویرایش/حذف)
تنها برای کاربر `dcc` دیده می‌شوند؛ بقیه پیام «دسترسی شما فقط مشاهده است» را می‌بینند.

### ۸-۴. کلید هوش مصنوعی کجا وارد می‌شود؟

**بارگذاری اسناد به کلید هوش مصنوعی نیاز ندارد.** خط لوله پیش‌فرض این است: لایه متن PDF →
قواعد R01–R22 → OCR (Tesseract `fas+eng`) → در موارد مبهم، در صورت فعال بودن، VLM.

اگر می‌خواهید لایه کمکی VLM را فعال کنید، دو راه دارید.

**راه سریع (موقت، بدون ری‌استارت):** در صفحهٔ «بارگذاری مدرک» دکمهٔ «کلید هوش مصنوعی» را
بزنید. آنجا سرویس‌دهنده (Gemini یا OpenAI-compatible)، آدرس و کلید را وارد می‌کنید و دکمهٔ
«ذخیره و آزمایش اتصال» بلافاصله نتیجه را با زمان پاسخ یا علت دقیق خطا نشان می‌دهد.
کلید فقط در حافظهٔ همین سرویس می‌ماند، در هیچ پاسخی برنمی‌گردد و تا ری‌استارت معتبر است.

**راه دائمی:** فایل `.env` را از `.env.example` بسازید و پر کنید:

```env
VLM_ENABLED=true
VLM_PROVIDER=openai                 # یا gemini
VLM_BASE_URL=http://vlm:8000/v1     # vLLM داخلی؛ Ollama: http://<ip-داخلی>:11434/v1
VLM_API_KEY=<کلید>
VLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
# برای Gemini:
# VLM_PROVIDER=gemini
# VLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
# VLM_MODEL=gemini-2.0-flash
```

سپس `docker compose up -d api worker` و در همان پروژه `vlm_fallback` را `true` کنید
(`PUT /projects/{id}/settings`).

بررسی وضعیت بدون افشای کلید:

* `GET /api/v1/system/capabilities` — OCR روشن/خاموش، VLM فعال/غیرفعال، «کلید تنظیم شده؟»، آدرس مجاز.
* `GET /api/v1/admin/ai-status` (ابرمستخدم) — همان + تست واقعی اتصال به endpoint.
* `GET/PUT /api/v1/admin/ai-config` و `POST /api/v1/admin/ai-config/test` (ابرمستخدم) —
  خواندن/نوشتن تنظیمات موقت و بررسی اتصال؛ کلید هرگز برگردانده نمی‌شود.
* در صفحهٔ «بارگذاری»، نوار «موتور استخراج» همین وضعیت را نشان می‌دهد.

**محدودیت امنیتی (آگاهانه):** آدرس داخلی (localhost، vlm، 10.x، 192.168.x، 172.x، `.local`،
`.internal`) بدون تأیید پذیرفته می‌شود. آدرس عمومی — از جمله `*.googleapis.com` — فقط وقتی
کار می‌کند که مدیر در همان درخواست `confirm_remote_acknowledged=true` بفرستد؛ یعنی ارسال
مدارک محرمانه به بیرون یک تأیید صریح انسانی می‌خواهد، نه یک پیش‌فرض.

### ۸-۲. هشت حساب لازم برای این مرحله

`SEED_DEMO=true python -m app.bootstrap` این حساب‌ها را می‌سازد (رمز همه: `Demo1234!`):

| # | ورود | نقش | مسئولیت |
|---|---|---|---|
| ۱ | `submitter@example.com` | `submitter` | ایجاد برگه ارسال و ارسال به کنترل اسناد |
| ۲ | `dcc@example.com` | `dcc` | کنترل محتوا/پیوست و ارسال به مدیریت |
| ۳ | `pm@example.com` | `project_manager` | مرحله مدیریت (هر یک از دو مدیر می‌تواند اقدام کند) |
| ۴ | `em@example.com` | `engineering_manager` | بررسی و ارجاع به دیسیپلین |
| ۵ | `ele@example.com` | `reviewer` + دیسیپلین ELE | کارشناس برق |
| ۶ | `civ@example.com` | `reviewer` + دیسیپلین CIV | کارشناس سیویل |
| ۷ | `arc@example.com` | `reviewer` + دیسیپلین ARC | کارشناس معماری |
| ۸ | `mec@example.com` | `reviewer` + دیسیپلین MEC | کارشناس تاسیسات |

به‌علاوه `admin@example.com` / `Admin123!` به‌عنوان ابرکاربر. فهرست کامل اعضا با نقش و
دیسیپلین از `GET /api/v1/projects/{id}/members` خوانده می‌شود و همان چیزی است که دیالوگ
«ارجاع به دیسیپلین» را پر می‌کند.

---

## ۹. نقش‌ها و دسترسی

| نقش | توانایی |
|---|---|
| `system_admin` | مدیریت کاربران، ممیزی، وضعیت سیستم |
| `project_manager` | تنظیمات پروژه، تأیید برگه خارجی، تصمیم‌های نهایی |
| `dcc` | ایمپورت MDL، صف بازبینی، ثبت بازنگری، صدور برگه ارسال |
| `engineering_manager` | بررسی برگه ارسال و ارجاع به کارشناس دیسیپلین |
| `reviewer` | مشاهده مدارک، ثبت نظر (بدون توانایی ثبت بازنگری)؛ با `discipline_id` کارشناس همان دیسیپلین است |
| `submitter` | بارگذاری فایل و مشاهده وضعیت مدارک خود |

ACL پروژه‌ای است: کاربر غیرعضو، پروژه را `404` می‌بیند (نه `403`) تا وجود آن افشا نشود.

---

## ۱۰. نقشه فایل‌ها

```
.
├── .env.example                 # همه متغیرهای محیطی با توضیح فارسی/انگلیسی
├── docker-compose.yml           # api · worker · beat · postgres · redis · minio · frontend · nginx · vlm(profile)
├── nginx/nginx.conf             # SPA + پروکسی API + محدودیت حجم آپلود
├── docs/
│   ├── README.md                # خلاصه طراحی
│   ├── 01-scope-and-requirements.md
│   ├── 02-architecture.md
│   └── 03-reference-system-review.md   # بررسی skill سامانه مرجع + هم‌ترازی
├── backend/
│   ├── Dockerfile               # python:3.12 + tesseract-ocr(fas,eng) + libs
│   ├── requirements.txt / requirements-dev.txt
│   ├── alembic.ini · alembic/versions/0001_initial.py
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py              # FastAPI app، CORS، هندلر خطای فارسی، /health
│   │   ├── bootstrap.py         # ساخت مدیر + پروژه نمونه + قواعد DDM
│   │   ├── core/               # config · security (JWT/bcrypt) · auth_providers (local/ldap/oidc)
│   │   ├── db/                 # Base (UUID/JSONB) · session
│   │   ├── models/             # 26 جدول: core · documents · transmittal · enums
│   │   ├── schemas/            # Pydantic v2
│   │   ├── api/
│   │   │   ├── deps.py         # کاربر جاری، عضویت پروژه، نقش‌ها
│   │   │   └── routes/         # auth · projects · mdl · uploads · review · documents · transmittals
│   │   │                       # ddm · comments · files · notifications · dashboard · admin
│   │   ├── services/           # normalization · code_matching · revisions · decision_engine
│   │   │                       # transmittal_service · mdl_import · numbering · ddm · documents
│   │   │                       # ingest · processing · rendering · storage · acl · audit · notification
│   │   ├── extraction/         # base · title_block · ocr · vlm · pipeline
│   │   ├── workers/            # celery_app · tasks
│   │   └── utils/jalali.py
│   └── tests/                  # 149 تست
└── frontend/
    ├── Dockerfile              # stage build → nginx
    ├── nginx-spa.conf
    ├── vite.config.ts          # پروکسی /api و /health، host 0.0.0.0
    ├── tailwind.config.js      # پالت برند + فونت وزیرمتن
    └── src/
        ├── lib/                # api (fetch + refresh + blob) · auth · project · format (شمسی) · types
        ├── components/         # ui · Layout · DecisionBadge · PdfViewer
        └── pages/              # Login · Dashboard · MDLLibrary · Upload · ReviewQueue
                                # ReviewDetail · Transmittals · TransmittalDetail · CommentList
```

---

## ۱۱. روش کار عامل هوش مصنوعی (SKILLS)

دو بسته مهارت بیرونی در `skills/vendor/` نگهداری می‌شوند و روش کار ما را تعیین می‌کنند؛
فایل `AGENTS.md` در ریشه مخزن، «فعال‌ساز» آن‌هاست و باید ابتدای هر نشست خوانده شود.

| بسته | منبع | نقش در این پروژه |
|---|---|---|
| `skills/vendor/task-observer/` | `one-skill-to-rule-them-all-main.zip` | ثبت مشاهده‌ها، بازبینی هفتگی، بهبود فرایند |
| `skills/vendor/superpowers/skills/` | `superpowers-main.zip` | TDD، ریشه‌یابی سیستماتیک، برنامه‌نویسی، تأیید پیش از اعلام پایان |

- **لاگ مشاهده‌ها:** `skill-observations/observation-log/` — هر مشاهده یک فایل با
  frontmatter و بدنه Issue → Improvement → Principle. تا امروز ۷ مشاهده ثبت شده که
  چهار موردش به اصلاح واقعی کد انجامید.
- **برنامه‌های اجرایی:** `docs/superpowers/plans/` — مثل
  `2026-10-24-comment-review-cycle.md`.
- **دو انحراف آگاهانه** در `AGENTS.md` مستند شده است (نبود چرخه آیین سومین کار، و
  استفاده نکردن از git worktree چون نشست به شاخه `arena/...` گره خورده است).

## ۱۱. روش کار عامل هوش مصنوعی (AGENTS.md و مهارت‌ها)

`AGENTS.md` در ریشه مخزن، «روش کار» را تثبیت می‌کند: کدام مهارت کِی اجرا می‌شود،
دستورهای اعتبارسنجی، و قواعدی که بر هر مهارتی مقدم‌اند. دو بستهٔ مهارت به‌صورت
فقط‌خواندنی در `skills/vendor/` نگه‌داری می‌شوند:

| بسته | مسیر | کاربرد |
|---|---|---|
| `task-observer` | `skills/vendor/task-observer/` | ثبت مشاهده‌ها و بازبینی هفتگی؛ ارتقای الگوهای تکرارشده به قاعده |
| `superpowers` | `skills/vendor/superpowers/` | چرخهٔ مهندسی: طراحی → نقشه → TDD → اعتبارسنجی → بازبینی |

```bash
bash skills/vendor/superpowers/skills/using-superpowers/scripts/skill-load.sh
python3 skills/vendor/task-observer/scripts/session-start-scan.sh
```

مشاهده‌ها در `skill-observations/observation-log/` با شماره و frontmatter ثبت می‌شوند؛
نقشه‌های اجرایی در `docs/superpowers/plans/`. محدودیت صادقانه: این بسته‌ها کامل و
تشریفاتی‌اند و ما آن‌ها را در عمقی اجرا می‌کنیم که هزینه‌اش را پس بدهد (مشاهدهٔ `0006`).

## ۱۰-۱. ظاهر و تجربه کاربری

رابط کاربری با همان زبان بصری سامانهٔ سازمانی موجود طراحی شده است:

| عنصر | توضیح |
|---|---|
| نوار بالای صفحه | مسیر رنگی پرچم، ماست‌هد با عنوان «سامانه مدیریت اسناد مهندسی»، نوار سبز، نوار ناوبری سربی‌رنگ |
| منوی کناری | سایدبار نقره‌ای در سمت راست با گروه‌های `Engineering Documents` و `Documents Center` و شمارنده وظایف |
| جدول‌ها | سرستون سرمه‌ای، خطوط شبکه‌ای، ردیف‌های یک‌درمیان، ارتفاع فشرده (۱۰٫۵px) و انتخاب ردیف با کلیک |
| فرم‌ها | نوار عنوان آبی بخش‌ها (`Transmittal Information`) و جدول‌های `Label / Value` |
| پنجره‌ها | نوار عنوان خاکستری با `✕` و `▣` در لبه چپ، عنوان وسط‌چین سرمه‌ای، بدنه کرم روشن و **نوار سبز پایین** — در همه صفحات یکسان |
| ریبون | نوار آیکون + عنوان فارسی زیر هر آیکون (`بازخوانی`، `یادآوری`، `گردش مدرک`، `ابطال`…)؛ دکمه‌های فاز ۲ غیرفعال و با tooltip مشخص |
| فیلدها | جدول دوستونی `Label/Value` با سلول برچسب **کرم‌خاکی** و مقدار سفید، خطوط ۱px خاکی — یکسان در فرم، جدول و پنجره‌ها |
| تب‌ها | نوار سرمه‌ای با تب برجسته روشن: Letters · Comment List · Native Files · Revision Index · Document Records · Reply Sheet · Delivery |
| فونت و رنگ | Tahoma/B Nazanin با بازگشت به Vazirmatn — بدون وابستگی به فونت آنلاین |

### کلاس‌ها و کامپوننت‌های ظاهر (یک منبع حقیقت)

| CSS | کامپوننت | کاربرد |
|---|---|---|
| `.window`, `.window-title`, `.window-body`, `.window-strip` | `Window` (`components/window.tsx`) | چارچوب پنجره برای هر صفحه/دیالوگ |
| `.field-grid`, `.fg-label`, `.fg-value` | `FieldGrid` + `FField` | فرم‌های برچسب/مقدار |
| `.ribbon`, `.ribbon-btn` | `Ribbon` | نوار عملیات آیکونی |
| `.tabstrip`, `.tab` | `Tabs` | نوار تب سرمه‌ای |
| `.kv-table` | — | همان قالب فیلدها برای جدول‌های کلید/مقدار |
| `.icon-btn`, `.status-dot`, `.group-band` | `IconButton`, `StatusLamp`, `GroupBand` | دکمه‌های ریز جدول، چراغ وضعیت، نوار گروه |

هر کارت دارای سرصفحه (`.card` + `.card-header`) خودبه‌خود همان نوار عنوان پنجره را می‌گیرد؛
پس هر صفحه‌ای که اضافه شود، بدون کار اضافه هم‌ظاهر بقیه است.

صفحهٔ «کارتابل من» همان ستون‌های سامانهٔ فعلی را دارد: تاریخ دریافت (شمسی + ساعت)، عنوان مدرک، عملکرد ارجاع،
فرستنده، نوع مدرک، مهلت پاسخ، پاسخ، عملیات؛ به‌همراه نوار عملیات پایین جدول (مشاهده مدرک، تأییدیه دریافت،
گردش مدرک، دریافت PDF). «گردش مدرک» یک دیالوگ واقعی با اطلاعات گیرندگان و تاریخ تأییدیه باز می‌کند.

---

## ۱۱. API

مستندات تعاملی: `http://localhost:8000/api/docs`

| گروه | نمونه مسیرها |
|---|---|
| احراز هویت | `POST /auth/login` · `POST /auth/refresh` (چرخش توکن) · `POST /auth/logout` · `GET /auth/me` · `GET /auth/transport-check` (عیب‌یابی) · `POST /auth/change-password` |
| پروژه | `GET/POST /projects` · `GET/PATCH /projects/{id}` · `GET /projects/{id}/dashboard` |
| MDL | `POST /projects/{id}/mdl/preview` · `POST /projects/{id}/mdl/import` · `POST /projects/{id}/mdl/items` (دستی) · `PATCH/DELETE /projects/{id}/mdl/items/{id}` · `GET /projects/{id}/mdl/items` · `GET /projects/{id}/mdl/export` — همهٔ نوشتن‌ها فقط `dcc` |
| سیستم | `GET /system/capabilities` (وضعیت OCR/VLM بدون کلید) · `GET /admin/ai-status` (ابرمستخدم، با تست اتصال) |
| بارگذاری | `POST /projects/{id}/upload-sessions` · `POST /upload-sessions/{id}/files` · `POST /upload-sessions/{id}/commit` |
| بازبینی | `GET /projects/{id}/review-queue` · `GET /extraction-results/{id}` · `POST /extraction-results/{id}/review` · `POST /projects/{id}/review/bulk` |
| مدارک | `GET /projects/{id}/documents` · `GET /revisions/{id}` · `GET /mdl-items/{id}/revisions` |
| برگه ارسال | `GET/POST /projects/{id}/transmittals` (`?mine=true` = کارتابل من) · `POST /transmittals/{id}/issue` · `/forward` (کنترل اسناد) · `/refer` (مدیریت) · `/referrals/{rid}/complete` (کارشناس) · `GET /transmittals/{id}/referrals` · `/acknowledge` · `/cancel` · `/remind` · `GET /pdf` |
| اعضای پروژه | `GET /projects/{id}/members` (نقش + دیسیپلین؛ خوراک دیالوگ ارجاع) |
| DDM | `GET/POST /projects/{id}/ddm/rules` · `PATCH/DELETE …/{rule_id}` · `POST /projects/{id}/ddm/simulate` |
| نظرات | `GET /revisions/{id}/comment-lists` · `POST /revisions/{id}/comments` · `POST /comments/{id}/respond` · `/close` |
| فایل | `GET /files/source-files/{id}` · `/files/sheets/{id}/thumbnail` · `/files/sheets/{id}/title-block` · `/files/results/{id}/raw-text` |
| اعلان | `GET /notifications` · `POST /notifications/{id}/read` · `POST /notifications/read-all` |
| مدیریت | `GET /admin/status` · `GET/POST /admin/users` · `PATCH /admin/users/{id}/active` · `GET /admin/audit` |

پاسخ خطا به‌صورت `{"detail": "پیام فارسی"}` یا `{"detail": {"message": "...", "issues": [...]}}` است و رابط کاربری هر دو حالت را نمایش می‌دهد.

---

## ۱۲. چیزهایی که در فاز ۱ نیستند (شفاف)

* پشتیبانی DWG / Revit / IFC و مقایسه خودکار نسخه‌ها.
* امضای دیجیتال برگه ارسال و گردش تأیید چندسطحی (فقط یک سطح تأیید مدیر پروژه پیاده شده است).
* صفحات مدیریت کاربران و ویرایش قواعد DDM در UI — در فاز ۱ این دو از طریق API (`/admin/*` و `/ddm/*`، مستند در `/api/docs`) انجام می‌شود؛ رابط عملیاتی (MDL، بارگذاری، بازبینی، برگه ارسال، نظرات) کامل است.
* جست‌وجوی تمام‌متن فارسی (PostgreSQL FTS بدون پیکربندی `fa` نیاز به افزونه دارد؛ فعلاً جست‌وجوی `ILIKE` روی کد/عنوان).
* آموزش مدل اختصاصی: تصحیح‌های کارشناس ذخیره می‌شوند (`corrections`) اما حلقه بازآموزی در فاز ۲ است.

---

## ۱۳. امنیت

* رمزها با `bcrypt`، توکن‌های `HS256` کوتاه‌عمر، Refresh اختیاری (با چرخش توکن).
* توکن دسترسی از سه مسیر پذیرفته می‌شود: هدر `Authorization: Bearer`، هدر `X-Access-Token` و کوکی `HttpOnly` (`edms_access_token`) — تا پروکسی معکوس یا واسط سازمانی که یکی از این‌ها را حذف می‌کند، نشست کاربر را از بین نبرد. برای عیب‌یابی، `GET /api/v1/auth/transport-check` نشان می‌دهد توکن از کدام مسیر رسیده است.
* هر فایل در MinIO/S3 با کلید مستقل و دسترسی از طریق endpoint احراز‌شده (`/files/...`) — دسترسی مستقیم به bucket لازم نیست.
* بررسی نوع MIME + سقف حجم (`MAX_UPLOAD_MB`) + محاسبه SHA-256 برای کشف تکراری + قلاب آنتی‌ویروس (`AV_ENABLED`, clamd).
* ثبت ممیزی برای عملیات حساس (ثبت بازنگری، صدور/ابطال برگه ارسال، تغییر عضویت، تغییر تنظیمات) در جدول `audit_logs` با IP، کاربر و payload.
* هیچ تماس بیرونی به سرویس‌های AI؛ VLM فقط از آدرس داخلی تنظیم‌شده.

---

## ۱۴. وضعیت اعتبارسنجی

| مؤلفه | وضعیت |
|---|---|
| تست‌های بک‌اند | ✅ ۱۴۹ passed |
| مهاجرت Alembic | ✅ `upgrade head` و `downgrade base` روی PostgreSQL-سازگار بررسی شد |
| OpenAPI | ✅ ۶۴ مسیر، بدون خطای واردکردن |
| فرانت‌اند `tsc --noEmit` | ✅ بدون خطا |
| فرانت‌اند `vite build` | ✅ موفق (JS ۶۱۶KB، CSS ۳۱KB، worker پی‌دی‌اف ۱٫۳MB) |
| Docker Compose | آماده اجرا؛ در این محیط اجرا نشده است (دسترسی به Docker در سندباکس محدود است) |

---

## ۱۵. عیب‌یابی سریع

| نشانه | علت رایج | راه‌حل |
|---|---|---|
| `ModuleNotFoundError: psycopg` | `DATABASE_URL` پیش‌فرض Postgres و درایور نصب نیست | `pip install "psycopg[binary]"` یا تنظیم `DATABASE_URL` روی SQLite |
| پردازش مدرک به مرحله بازبینی نمی‌رسد | worker بالا نیست یا Redis در دسترس نیست | `CELERY_TASK_ALWAYS_EAGER=true` برای توسعه، یا راه‌اندازی سرویس `worker` |
| OCR خطای Tesseract می‌دهد | نبود بسته زبان فارسی | `apt-get install tesseract-ocr tesseract-ocr-fas` (در Dockerfile هست) |
| PDF برگه ارسال تولید نمی‌شود | نبود WeasyPrint/ReportLab | `pip install weasyprint reportlab` (در requirements هست؛ در محیط بدون کتابخانه‌های سیستمی قابل نصب نیست) |
| ورود موفق است ولی بلافاصله «احراز هویت لازم است.» می‌آید | پروکسی معکوس هدر `Authorization` را حذف می‌کند | مرورگر را یک‌بار رفرش کنید؛ توکن از کوکی هم ارسال می‌شود. برای تشخیص: `GET /api/v1/auth/transport-check` |
| پیام «احراز هویت لازم است.» در مرورگر | پورت **۸۰۰۰** را باز کرده‌اید؛ آن پورت فقط API است و هر مسیرش توکن JWT می‌خواهد | پورت **۵۱۷۳** (توسعه) یا **۸۰۸۰** (Docker) را باز کنید؛ صفحه ریشه API حالا همین را توضیح می‌دهد |
| پیام «احراز هویت لازم است.» بعد از راه‌اندازی مجدد سرور | توکن ذخیره‌شده در مرورگر با `SECRET_KEY` قدیمی امضا شده است | یک‌بار خارج و دوباره وارد شوید (برنامه خودش نشست را پاک می‌کند و پیام «نشست منقضی شده» را نشان می‌دهد) |
| ورود با خطای ۴۲۲ | ایمیل با دامنه رزرو‌شده (مثل `example.invalid`) | دامنه واقعی کاربر را وارد کنید |

---

**نسخه:** 1.0.0 · **تقویم نمایشی:** شمسی (Asia/Tehran) · **زبان:** فارسی (RTL) با برچسب‌های دوزبانه
