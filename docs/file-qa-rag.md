# پرسش و پاسخ از فایل‌ها (Persian RAG v2)

این قابلیت افزایشی و خاموش است. مسیرهای جدید از credential فعال و رمزنگاری‌شدهٔ
بخش AI Upload استفاده می‌کنند؛ `FILE_QA_MODEL` فقط نام مدل را override می‌کند و
کلید تازه‌ای ایجاد نمی‌کند.

## فعال‌سازی امن

1. از پایگاه‌داده backup بگیرید و مطمئن شوید PostgreSQL/pgvector در دسترس است.
   Compose فعلی از `pgvector/pgvector:pg16` استفاده می‌کند.
2. migration را اجرا کنید:

   ```bash
   cd backend
   alembic upgrade head
   ```

   migration `0015_persian_rag_v2` جدول‌های `document_chunks_v2`,
   `rag_index_jobs`, `file_qa_sessions` و `file_qa_messages` را اضافه می‌کند.
   جدول `document_chunks`، بردارهای ۷۶۸بعدی و indexهای قبلی دست‌نخورده می‌مانند.
3. در `.env` مقدارهای زیر را تنظیم کنید و سرویس backend/worker را restart کنید:

   ```dotenv
   FILE_QA_ENABLED=true
   FILE_QA_EMBEDDING_MODEL=intfloat/multilingual-e5-small
   FILE_QA_EMBEDDING_DIMENSIONS=384
   # اختیاری؛ در حالت خالی model Credential فعال استفاده می‌شود
   FILE_QA_MODEL=
   ```

4. در UI بخش AI Upload یک Credential فعال و مجاز را تنظیم/آزمایش کنید. API key
   هرگز به frontend، URL یا log فرستاده نمی‌شود.
5. برای backfill مدیر سیستم از `POST /api/v1/file-qa/admin/rebuild` با `file_ids`
   (حداکثر ۱۰ فایل در هر درخواست) یا `project_id` استفاده کند. jobها در
   `rag_index_jobs` resumable هستند و بین chunkها commit/throttle می‌شوند؛ وضعیت
   از `GET /api/v1/file-qa/admin/jobs` قابل مشاهده است.

DOCX از paragraph و table row استخراج می‌شود و PDF از page به همراه OCR موجود
پشتیبانی می‌شود. متن اصلی برای snippet/display و متن نرمال‌شده برای retrieval جدا
ذخیره می‌شوند. chunkها حداکثر ۲۲۰ کلمه و با overlap برابر ۴۵ کلمه هستند.

## API و دسترسی

* `GET /api/v1/file-qa/status` — وضعیت flag، backend، مدل و تعداد فایل‌های قابل دسترس
* `GET /api/v1/file-qa/documents?q=...&project_id=...` — picker با ACL فعلی
* `POST /api/v1/file-qa/ask` — JSON برای فایل‌های EDMS؛ multipart با `file` برای یک
  PDF/DOCX موقت؛ در هر پرسش حداکثر ۱۰ فایل انتخابی
* `GET /api/v1/file-qa/history/{session_id}` — فقط صاحب نشست
* `POST /api/v1/file-qa/clear` — تاریخچه را با علامت inactive پاک می‌کند و رکوردهای
  audit/history را به‌صورت مخرب حذف نمی‌کند
* `POST /api/v1/file-qa/admin/rebuild` و `GET /api/v1/file-qa/admin/jobs` — فقط مدیر سیستم

source cardها فقط filename/page/excerpt و لینک نسبی authenticated به فایل EDMS را
دارند. فایل موقت لینک ذخیره‌شده ندارد و پس از پایان درخواست حذف می‌شود.

## SQLite و rollback

در SQLite، server با خطا متوقف نمی‌شود و status این پیام را نشان می‌دهد:

```text
پرسش و پاسخ پیشرفته فقط با PostgreSQL فعال است
```

در این حالت retrieval محدود LIKE/keyword ممکن است کار کند، اما semantic vector
فعال نمی‌شود. برای خاموش‌کردن بدون حذف داده:

```dotenv
FILE_QA_ENABLED=false
```

برای rollback migration فقط migration افزودهٔ جدید را برگردانید:

```bash
alembic downgrade 0014_search_indexing
```

این downgrade فقط جدول‌ها و indexهای ساخته‌شده توسط migration 0015 را حذف می‌کند؛
extensionهای pgvector/pg_trgm و داده‌های جست‌وجوی قدیمی حذف نمی‌شوند. برای بازگشت
کامل قابلیت بعداً دوباره `alembic upgrade head` و `FILE_QA_ENABLED=true` را اجرا کنید.

## کنترل prompt و fallback

پاسخ‌دهی با temperature صفر و context محدود به قطعه‌های مجاز انجام می‌شود؛ متن
سند به‌عنوان data علامت‌گذاری شده و دستورهای داخل سند اختیار system prompt را
ندارند. نبودن قطعهٔ کافی یا پاسخ خارج از context دقیقاً به این متن تبدیل می‌شود:

```text
بر اساس مدارک موجود پاسخی یافت نشد.
```
