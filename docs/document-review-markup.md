# بازبینی نشانه‌گذاری PDF / Document Review Markup

این قابلیت برای پیوست‌های PDF ترانسمیتال افزایشی است. فایل اصلی هرگز با خروجی نشانه‌گذاری جایگزین نمی‌شود؛ جلسه، هش و کلید ذخیره فایل اصلی را نگه می‌دارد و هنگام ثبت نهایی دو فایل جدید می‌سازد:

* `<originalname>_COMMENT_<user>_<date>.pdf` — نسخه PDF مسطح‌شده با نشانه‌ها؛
* `<originalname>_COMMENT_SHEET_<user>_<date>.pdf` — Comment Sheet فارسی/انگلیسی.

## دسترسی و چرخه کار

`REVIEW_MARKUP_ENABLED=true` پیش‌فرض است. قابلیت `document.comment` از ACL نقش پروژه استفاده می‌کند و به‌طور پیش‌فرض برای `reviewer`، `dcc`، `project_control`، `pmo`، `project_manager` و `system_admin` فعال است. مشاهده نیز پیش از خواندن داده یا فایل، ACL ترانسمیتال را بررسی می‌کند.

هر نویسنده برای هر پیوست یک پیش‌نویس شخصی دارد. نویسنده می‌تواند ذخیره و ثبت نهایی کند؛ بعد از ثبت نهایی جلسه و نشانه‌ها فقط خواندنی هستند. مالک مدرک یا مسئول کنترل می‌تواند برای هر نشانه پاسخ با وضعیت باز، پذیرفته، ردشده یا حل‌شده ثبت کند. حذف جلسه و خروجی‌ها فقط از مسیر مدیریتی و با audit انجام می‌شود.

## API

تمام مسیرها زیر `/api/v1` و نیازمند نشست احراز هویت هستند:

* `POST /markups` با `{transmittal_id, file_id}`؛
* `GET /markups?file_id=...` و `GET /markups/{id}`؛
* `PUT /markups/{id}/annotations` برای ذخیره جایگزین‌پذیر پیش‌نویس؛
* `POST /markups/{id}/submit`؛
* `POST /markups/{id}/registration-opened` برای قفل‌شدن حذف پس از بازکردن فرم Comment Registration؛
* `POST /markups/{id}/annotations/{number}/reply`؛
* `DELETE /markups/{id}` برای مدیر.

دانلود خروجی‌ها مسیر موجود پیوست ترانسمیتال را reuse می‌کند:
`GET /transmittals/{transmittal_id}/attachments/{attachment_id}/download`؛
خروجی‌ها همچنان با مسیر موجود پیوست ترانسمیتال دریافت می‌شوند و endpoint جدید فقط وضعیت مشاهده فرم را ثبت می‌کند؛ ACL/Content-Disposition موجود حفظ می‌شود.

نقاط نشانه‌گذاری در JSON با مختصات نرمال‌شده `0..1` ذخیره می‌شوند. جدول‌های جدید فقط `markup_sessions` و `markup_annotations` هستند و migration `0016_document_markup` در downgrade فقط همین دو جدول و ایندکس‌هایشان را حذف می‌کند.

## اجرای آفلاین و اتصال به Comment Registration

دکمه `ایجاد کپی برای کامنت` فقط برای کاربر دارای capability `document.comment` و زمانی که flag روشن است نمایش داده می‌شود. این دکمه همان modal موجود Attachment Viewer در فضای کاری Transmittal را به حالت markup می‌برد؛ مسیر جداگانه `/markups/new` برای سازگاری لینک‌های قدیمی همچنان موجود است، اما مسیر اصلی کاربر از viewer خارج نمی‌شود. PDF.js، worker، cMap و standard fonts از assets محلی frontend بارگیری می‌شوند و ویرایشگر روی canvas PDF.js و overlay برداری SVG کار می‌کند.

پیش‌نویس تغییرات در حافظه رابط کاربری و یک backup محدود localStorage نگه‌داری می‌شود؛ ذخیره خودکار و دستی خطا را به‌صورت قابل retry نمایش می‌دهند و هنگام خروج با تغییرات ذخیره‌نشده هشدار داده می‌شود. پس از ثبت نهایی، خروجی‌ها با یک لینک محدود به `/documents/{revision_id}/comments?markup_session_id=...` به فرم موجود `Comment Registration` تحویل می‌شوند؛ فرم فایل‌های خروجی را از مسیر authenticated به صورت موقت در حافظه `File` می‌سازد و با بستن modal آن‌ها را پاک می‌کند. ساخت Comment Sheet از فونت Persian-capable موجود در `backend/app/assets/fonts/DejaVuSans.ttf` استفاده می‌کند و به CDN یا API خارجی نیاز ندارد.

## عدم تغییر فایل اصلی

هنگام ایجاد جلسه SHA-256 فایل اصلی ثبت می‌شود. در submit دوباره hash و تعداد صفحات بررسی می‌شود. خروجی با PyMuPDF در حافظه ساخته و در storage با کلید جدید ذخیره می‌شود؛ رکورد پیوست PDF اصلی، کلید و bytes آن دست‌نخورده می‌ماند. دو خروجی ابتدا فقط در metadata ترانسمیتال و جلسه بازبینی به‌صورت staging احراز هویت‌شده نگه‌داری می‌شوند و در Attachment List اصلی نمایش داده نمی‌شوند؛ پس از تکمیل و ذخیره صریح فرم Comment Registration، به Comment/CommentAttachment موجود متصل می‌شوند تا در Comment List و Related/Attachment viewهای فعلی قابل دسترسی باشند.
