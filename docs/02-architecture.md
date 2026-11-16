# ۰۲ — معماری سیستم

> شناسه: **EDMS-AI-ARCH-002** · مرجع نیازمندی: `01-scope-and-requirements.md`

---

## ۱. اصول حاکم بر معماری

| اصل | پیامد عملی |
|---|---|
| **تفکیک مسیر کند از سریع** | آپلود و استخراج در Worker؛ API هرگز منتظر OCR/VLM نمی‌ماند |
| **قاعده بر مدل مقدم است** | خروجی AI همیشه از فیلتر قواعد قطعی می‌گذرد (سند ۰۳) |
| **همه‌چیز قابل بازسازی** | ذخیره شواهد: متن خام، برش تصویر، bbox، نسخه مدل و پرامپت |
| **دروازه انسانی قابل حذف است، نه قابل دور زدن** | دو نقطه تأیید (DCC و صدور) با تنظیم سطح اتوماسیون |
| **مرز ماژول = مرز آینده سرویس** | ماژول‌ها با API داخلی و رویداد صحبت می‌کنند، نه با جدول یکدیگر |
| **داده پروژه ایزوله** | Row-Level Security در Postgres + Prefix در Storage |
| **Idempotency همه‌جا** | آپلود، پردازش، ارسال اعلان، صدور Transmittal |

---

## ۲. نمای زمینه (Context)

```mermaid
flowchart LR
    ORIG["پیمانکار / Originator"] --> WEB
    DCC["کنترل مدارک DCC"] --> WEB
    REV["کارشناسان رشته"] --> WEB
    PM["مدیر پروژه"] --> WEB
    CLI["کارفرما"] --> WEB

    WEB["EDMS-AI<br/>پورتال وب"] --> AD["Active Directory / LDAP"]
    WEB --> MAIL["SMTP / Exchange"]
    WEB --> SMS["درگاه پیامک"]
    WEB --> PROJ["Project Server / MSP<br/>(اختیاری)"]
    WEB --> AI["سرویس AI داخلی<br/>OCR + VLM"]
    WEB --> AV["ClamAV"]
    WEB --> STORE["Object Storage<br/>MinIO / S3"]
```

---

## ۳. معماری ظرف (Container)

```mermaid
flowchart TB
    subgraph EDGE["لبه"]
      RP["Reverse Proxy<br/>Nginx / IIS ARR<br/>TLS, Rate limit"]
    end

    subgraph APP["Application Layer (Stateless, x2)"]
      UI["Web UI<br/>Next.js (RTL)"]
      API["Core API<br/>NestJS<br/>MDL / Docs / Transmittal / DDM / WF"]
      BFF["BFF + Auth<br/>JWT, RBAC/ABAC"]
    end

    subgraph WORK["Worker Layer"]
      W1["Ingest Worker<br/>hash, virus, split, thumbnail"]
      W2["Extraction Worker<br/>text → OCR → VLM"]
      W3["Transmittal/Notify Worker<br/>PDF, email, SMS, reminders"]
    end

    subgraph DATA["Data Layer"]
      PG[("PostgreSQL 16<br/>+ JSONB + RLS")]
      RD[("Redis<br/>queue, cache, locks")]
      S3[("MinIO / S3<br/>files, crops, PDFs")]
      SR[("OpenSearch<br/>full text")]
    end

    AI["AI Runtime<br/>PaddleOCR / Tesseract<br/>vLLM + Qwen2.5-VL"]
    AV["ClamAV"]

    RP --> UI --> BFF --> API
    API --> PG
    API --> RD
    API --> S3
    API --> SR
    API -. enqueue .-> RD
    RD --> W1 & W2 & W3
    W1 --> S3
    W1 --> AV
    W2 --> AI
    W2 --> PG
    W2 --> S3
    W3 --> PG
    W3 --> S3
    W3 --> MAIL["SMTP"]
```

### نقش هر جزء

| جزء | مسئولیت | چه چیزی **نیست** |
|---|---|---|
| `Core API` | مالک داده دامنه، قواعد کسب‌وکار، مجوزدهی، تراکنش | محل اجرای پردازش سنگین |
| `Ingest Worker` | اعتبارسنجی فایل، ویروس‌اسکن، Hash، تفکیک شیت، Thumbnail | تصمیم‌گیری درباره مدرک |
| `Extraction Worker` | اجرای خط لوله استخراج و ذخیره شواهد | ثبت Revision یا ارسال |
| `Transmittal Worker` | شماره‌گذاری، تولید PDF، اعلان، یادآور | ساخت محتوای Transmittal (این کار API است) |
| `AI Runtime` | سرویس مدل (OCR/VLM) با API داخلی | دسترسی به پایگاه داده |
| `Scheduler` | کارهای زمانی: یادآور، Escalation، پاکسازی، Snapshot KPI | — |

---

## ۴. مرزهای ماژول (Modular Monolith)

```text
src/
├─ modules/
│  ├─ project/        پروژه، دیسیپلین، Unit، تنظیمات، تقویم کاری
│  ├─ mdl/            MDL، ایمپورت، نسخه‌بندی، Alias
│  ├─ ingest/         Upload Session، فایل، شیت، ویروس‌اسکن، Hash
│  ├─ ai/             قالب Title Block، پرامپت، مدل، اجرای استخراج، شواهد
│  ├─ matching/       نرمال‌سازی، تطبیق دقیق/فازی، کاندیدها
│  ├─ validation/     کاتالوگ قواعد، موتور اجرا، امتیاز اطمینان
│  ├─ documents/      مدرک، Revision، Supersede، Timeline
│  ├─ transmittal/    گروه‌بندی، شماره‌گذاری، صدور، PDF، تأییدیه
│  ├─ ddm/            ماتریس توزیع، شبیه‌ساز
│  ├─ review/         Comment List، Annotation
│  ├─ workflow/       گردش‌کار چندسطحی، کارتابل، Delegation
│  ├─ notify/         Template، کانال‌ها، ترجیحات، Outbox
│  ├─ reporting/      KPI، گزارش‌ها، Export
│  ├─ audit/          لاگ تغییرناپذیر
│  └─ identity/       کاربر، نقش، گروه، همگام‌سازی AD
└─ shared/            خطاها، DTO، رخدادها، تاریخ شمسی، Storage client
```

**قاعده مرزی**: هر ماژول فقط از طریق `Service` عمومی خودش و رویدادهای دامنه با بیرون حرف می‌زند. دسترسی مستقیم به جدول ماژول دیگر در کد ممنوع (با ESLint rule قابل اجرا).

---

## ۵. جریان اصلی: از آپلود تا Transmittal صادرشده

```mermaid
sequenceDiagram
    autonumber
    actor U as کاربر
    participant UI as Web UI
    participant API as Core API
    participant S3 as Object Storage
    participant Q as Queue (Redis/BullMQ)
    participant IW as Ingest Worker
    participant EW as Extraction Worker
    participant AI as AI Runtime
    participant DB as PostgreSQL
    participant TW as Transmittal Worker

    U->>UI: انتخاب فایل‌ها + هدف ارسال
    UI->>API: POST /upload-sessions
    API->>DB: ایجاد جلسه (Draft)
    UI->>API: PUT /sessions/{id}/files/{n} (chunked)
    API->>S3: ذخیره بایت‌ها (multipart)
    API->>DB: ثبت فایل (status=Uploaded, sha256)
    API-->>UI: 202 + jobId
    API->>Q: enqueue ingest
    Q->>IW: پردازش
    IW->>S3: خواندن فایل
    IW->>IW: MIME, hash, ClamAV, split صفحات
    IW->>DB: ایجاد Sheet[] + Thumbnail + Crop اولیه
    IW->>Q: enqueue extract (per sheet)
    Q->>EW: استخراج
    EW->>AI: OCR / VLM (فقط در صورت نیاز)
    EW->>DB: ExtractionRun + Fields + evidence
    EW->>DB: ValidationIssue[] + decision
    EW->>API: (event) extraction.completed
    API->>DB: تطبیق MDL، ساخت پیش‌نویس Revision
    alt decision = GREEN و اتوماسیون فعال
        API->>DB: ثبت Revision + ساخت Transmittal پیش‌نویس
    else decision = YELLOW
        API->>DB: کار در صف بازبینی DCC
    else decision = RED
        API->>DB: QuarantineItem + دلیل
    end
    U->>UI: (DCC) تأیید و صدور
    UI->>API: POST /transmittals/{id}/issue
    API->>DB: تخصیص شماره (تراکنشی) + Outbox event
    API->>TW: enqueue issue
    TW->>S3: تولید PDF برگه + ذخیره
    TW->>DB: وضعیت Issued + خطوط
    TW->>Q: enqueue notifications
    TW-->>U: ایمیل/پیامک/کارتابل
```

### نقاط شکست و رفتار تعریف‌شده

| نقطه شکست | رفتار |
|---|---|
| قطع آپلود | Resumable؛ فایل ناقص هرگز ثبت نمی‌شود |
| فایل آلوده | جلسه ادامه می‌یابد، فایل قرنطینه و به کاربر اطلاع داده می‌شود |
| AI Runtime خاموش | Retry با Backoff؛ پس از N بار ← صف «استخراج دستی» (RED-عملیاتی، نه رد مدرک) |
| شکست تولید PDF | صدور Rollback می‌شود؛ شماره آزاد نمی‌شود (شماره مصرف‌شده باطل و ثبت می‌شود) |
| شکست ارسال ایمیل | Outbox با Retry؛ وضعیت `Notification.Failed` و نمایش در UI |
| قطع DB هنگام صدور | تراکنش واحد؛ هیچ Transmittal نیمه‌صادرشده‌ای باقی نمی‌ماند |

---

## ۶. صف، Idempotency و Outbox

**صف‌ها**: `ingest`, `extract:high`, `extract:bulk`, `transmittal`, `notify`, `report`.
- اولویت: کاربر تعاملی > دسته‌ای شبانه. سهمیه GPU با Semaphore در Redis.
- Retry: `ingest` ۳ بار، `extract` ۲ بار، `notify` ۵ بار با Backoff نمایی (۱m، ۵m، ۱۵m، ۱h، ۴h).
- Dead-letter queue با آلارم و صفحه مدیریت خطا در پنل Admin.

**Idempotency Key**:
| عملیات | کلید |
|---|---|
| پردازش فایل | `sha256(source_file)` |
| اجرای استخراج | `sha256(sheet_id + pipeline_version + template_version)` |
| ساخت Transmittal | `sha256(project + purpose + recipient_set + sorted(revision_ids))` |
| صدور | `transmittal_id` (اگر Issued باشد، دوباره اجرا نمی‌شود) |
| اعلان | `sha256(notification_type + entity_id + user_id + attempt_bucket)` |

**Outbox**: هر رخداد دامنه در همان تراکنش کسب‌وکار در `outbox_event` نوشته می‌شود؛ یک Dispatcher آن را به صف می‌برد. نتیجه: اتمیسیتی بین «تغییر داده» و «ارسال پیام».

---

## ۷. لایه ذخیره‌سازی

```text
s3://edms/{project_code}/
   incoming/{yyyy}/{mm}/{session_id}/{file_id}/original.pdf
   sheets/{sheet_id}/page-001.pdf
   thumbs/{sheet_id}/page-001.webp
   crops/{sheet_id}/titleblock.webp
   revisions/{doc_no}/{rev}/{file_id}.pdf
   transmittals/{number}/sheet.pdf
   comments/{revision_id}/annotations.json
```

**قواعد**:
- Bucket خصوصی؛ دسترسی فقط با لینک امضاشده (TTL پیش‌فرض ۱۵ دقیقه، حداکثر ۲ ساعت).
- Versioning فعال؛ حذف نرم (Soft delete) با `Object Lock` برای فایل‌های صادرشده.
- فایل اصلی هرگز بازنویسی نمی‌شود؛ هر بارگذاری، شیء جدید.
- محاسبه Hash روی **فایل ذخیره‌شده**، نه فایل کلاینت.

**پایگاه داده**: Postgres 16؛ جدول‌های حجیم (`audit_log`, `extraction_field`, `notification`) ماهانه Partition؛ ایندکس GIN روی `payload` و `pg_trgm` روی کد/عنوان مدرک.

**کش**: Redis برای تنظیمات پروژه، قالب Title Block، DDM حل‌شده، و نتیجه پرتو‌های تکراری؛ TTL ۵ دقیقه + ابطال رویدادی.

---

## ۸. پشته فنی پیشنهادی

| لایه | انتخاب | چرا / جایگزین |
|---|---|---|
| UI | **Next.js 14 + TypeScript + Tailwind + shadcn/ui** | RTL و کامپوننت‌های بالغ؛ جایگزین: React+Vite، Blazor |
| API | **NestJS (TypeScript)** | مرز ماژول، DI، اعتبارسنجی Schema؛ جایگزین: FastAPI+Django، .NET 8 |
| DB | **PostgreSQL 16** | JSONB برای payload، RLS برای ایزوله‌سازی، pg_trgm |
| Queue | **Redis 7 + BullMQ** | سبک و کافی؛ جایگزین: RabbitMQ |
| Storage | **MinIO** (یا S3/NAS) | On-prem، سازگار S3 |
| PDF | **PyMuPDF** (تحلیل)، **pdfplumber** (جدول/مختصات)، **Paged.js یا WeasyPrint** (تولید برگه) | فارسی/RTL در WeasyPrint با فونت Vazirmatn |
| OCR | **Tesseract 5 (fas+eng)** یا **PaddleOCR** | مقایسه دقت روی مجموعه طلایی |
| VLM | **Qwen2.5-VL-7B-Instruct** روی vLLM؛ گزینه ۳۲B برای دقت بالاتر | مدل باز، قابل اجرای داخلی |
| Search | **OpenSearch** (اختیاری فاز ۲) | جست‌وجوی متن کامل مدارک |
| Auth | **Keycloak** + LDAP Federation | SSO، نقش‌ها، MFA |
| Observability | **OpenTelemetry + Prometheus + Grafana + Loki** | — |

---

## ۹. استقرار (نمای فیزیکی)

```mermaid
flowchart LR
    subgraph DMZ["DMZ"]
      LB["Nginx TLS 1.3"]
    end
    subgraph APPZ["App Zone"]
      A1["app-01<br/>UI+API+Worker"]
      A2["app-02<br/>UI+API+Worker"]
    end
    subgraph AIZ["AI Zone (GPU)"]
      G1["gpu-01<br/>vLLM Qwen2.5-VL-7B"]
      G2["gpu-02 (اختیاری)"]
      OCR["ocr-01 CPU 16c"]
    end
    subgraph DATAZ["Data Zone"]
      PG1[("pg-primary")]
      PG2[("pg-replica")]
      MIN[("minio x4")]
      RED[("redis")]
    end
    LB --> A1 & A2
    A1 & A2 --> PG1
    PG1 --> PG2
    A1 & A2 --> MIN
    A1 & A2 --> RED
    A1 & A2 --> G1
    A1 & A2 --> OCR
```

- همه سرویس‌ها Stateless به‌جز داده؛ مقیاس افقی با افزودن `app-0N`.
- GPU فقط در AI Zone و بدون دسترسی به اینترنت؛ egress صفر (NFR-015).
- جداسازی شبکه: App ↔ DB فقط پورت ۵۴۳۲؛ App ↔ MinIO ۹۰۰۰؛ App ↔ GPU ۸۰۰۰ (gRPC/HTTP داخلی).

---

## ۱۰. مشاهده‌پذیری و بهره‌برداری

| متریک | آستانه هشدار |
|---|---|
| `extraction_duration_p95` | > ۹۰s |
| `queue_depth{extract}` | > ۲۰۰ |
| `ai_vlm_fallback_rate` | > ۳۵٪ (نشانه ضعف قالب/OCR) |
| `auto_accept_rate` | کاهش > ۱۰٪ هفتگی |
| `transmittal_issue_failures` | > ۰ |
| `notification_undelivered` | > ۵ در ساعت |
| `db_connections` | > ۸۰٪ سقف |

هر درخواست دارای `correlation_id` و هر کار صف دارای `job_id` قابل جست‌وجو در لاگ ساختاریافته است.

---

## ۱۱. چرا این معماری و نه چیز دیگر (خلاصه استدلال)

| گزینه | مزیت | درد | تصمیم |
|---|---|---|---|
| میکروسرویس کامل (۱۰+ سرویس) | استقلال تیم‌ها | هزینه DevOps، تراکنش توزیع‌شده، پیچیدگی برای تیم کوچک | ❌ فاز ۱ |
| مونولیت بدون Worker | سادگی | آپلود A0 + OCR، API را قفل می‌کند | ❌ |
| Serverless/Cloud AI | دقت بالا، بدون GPU | نقض محرمانگی، هزینه متغیر، وابستگی به اینترنت | ❌ پیش‌فرض |
| **مونولیت ماژولار + Worker + AI داخلی** | تعادل | نیاز به یک GPU و یک نفر DevOps | ✅ |
