import { useEffect, useMemo, useRef, useState } from "react";
import { renderAsync } from "docx-preview";
import type { DocumentAttachment } from "@/lib/types";
import { Button, ErrorBox, Spinner } from "@/components/ui";
import { PdfViewer } from "@/components/PdfViewer";
import { fileSize } from "@/lib/format";

const IMAGE_EXTENSIONS = new Set(["bmp", "gif", "ico", "jpeg", "jpg", "png", "svg", "webp"]);
const TEXT_EXTENSIONS = new Set([
  "csv",
  "css",
  "json",
  "log",
  "md",
  "rtf",
  "text",
  "tsv",
  "txt",
  "xml",
  "yaml",
  "yml",
]);
const AUDIO_EXTENSIONS = new Set(["aac", "flac", "m4a", "mp3", "ogg", "wav", "weba"]);
const VIDEO_EXTENSIONS = new Set(["avi", "m4v", "mkv", "mov", "mp4", "ogv", "webm"]);

type PreviewMode = "pdf" | "docx" | "image" | "text" | "audio" | "video" | "unsupported";

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : "";
}

function modeOf(attachment: DocumentAttachment): PreviewMode {
  const contentType = attachment.content_type.toLowerCase();
  const extension = extensionOf(attachment.filename);
  if (attachment.kind === "pdf" || contentType === "application/pdf" || extension === "pdf") return "pdf";
  if (extension === "docx" || contentType.includes("wordprocessingml.document")) return "docx";
  if (contentType.startsWith("image/") || IMAGE_EXTENSIONS.has(extension)) return "image";
  if (
    contentType.startsWith("text/") ||
    contentType.includes("json") ||
    contentType.includes("xml") ||
    contentType.includes("javascript") ||
    TEXT_EXTENSIONS.has(extension)
  ) {
    return "text";
  }
  if (contentType.startsWith("audio/") || AUDIO_EXTENSIONS.has(extension)) return "audio";
  if (contentType.startsWith("video/") || VIDEO_EXTENSIONS.has(extension)) return "video";
  return "unsupported";
}

function typeLabel(attachment: DocumentAttachment): string {
  return attachment.content_type || (attachment.kind === "pdf" ? "application/pdf" : "نوع ناشناخته");
}

function DocxViewer({ url }: { url: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;
    container.replaceChildren();
    setLoading(true);
    setError(null);

    void fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error("docx preview failed");
        return response.blob();
      })
      .then(async (blob) => {
        if (cancelled || !containerRef.current) return;
        await renderAsync(blob, containerRef.current, undefined, {
          className: "docx-preview",
          inWrapper: true,
          breakPages: true,
          useBase64URL: true,
        });
      })
      .catch(() => {
        if (!cancelled) setError("نمایش فایل Word ممکن نشد؛ فایل را دریافت کنید.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      container.replaceChildren();
    };
  }, [url]);

  return (
    <div className="relative max-h-[58vh] min-h-[20rem] overflow-auto border border-[#a8a284] bg-[#e9edf0] p-3">
      {loading ? <Spinner label="در حال آماده‌سازی پیش‌نمایش Word…" /> : null}
      {error ? <ErrorBox message={error} /> : null}
      <div ref={containerRef} className={loading || error ? "hidden" : "docx-host"} />
    </div>
  );
}

export function AttachmentPreview({
  attachment,
  url,
  loading,
  error,
  onDownload,
}: {
  attachment: DocumentAttachment;
  url: string | null;
  loading: boolean;
  error: string | null;
  onDownload: () => void;
}) {
  const mode = useMemo(() => modeOf(attachment), [attachment]);
  const [text, setText] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (mode !== "text" || !url) {
      setText(null);
      setTextError(null);
      setTextLoading(false);
      return;
    }
    setText(null);
    setTextError(null);
    setTextLoading(true);
    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error("text preview failed");
        return response.text();
      })
      .then((value) => {
        if (!cancelled) setText(value);
      })
      .catch(() => {
        if (!cancelled) setTextError("پیش‌نمایش متنی فایل ممکن نشد.");
      })
      .finally(() => {
        if (!cancelled) setTextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, url]);

  if (loading) return <Spinner label="در حال دریافت پیوست برای نمایش…" />;
  if (error) {
    return (
      <div className="space-y-2">
        <ErrorBox message={error} />
        <Button className="btn-sm" onClick={onDownload}>
          دریافت فایل
        </Button>
      </div>
    );
  }
  if (!url) return <p className="text-[10.5px] text-[#5c6b78]">فایل برای نمایش آماده نیست.</p>;

  if (mode === "pdf") {
    return (
      <div className="h-[58vh] min-h-[26rem] border border-[#a8a284]">
        <PdfViewer url={url} />
      </div>
    );
  }

  if (mode === "docx") {
    return <DocxViewer url={url} />;
  }

  if (mode === "image") {
    return (
      <div className="flex min-h-[20rem] max-h-[58vh] items-center justify-center overflow-auto border border-[#a8a284] bg-[#e9edf0] p-3">
        <img src={url} alt={attachment.filename} className="max-h-[54vh] max-w-full bg-white object-contain shadow-sm" />
      </div>
    );
  }

  if (mode === "text") {
    if (textLoading) return <Spinner label="در حال خواندن فایل متنی…" />;
    if (textError) {
      return (
        <div className="space-y-2">
          <ErrorBox message={textError} />
          <Button className="btn-sm" onClick={onDownload}>
            دریافت فایل
          </Button>
        </div>
      );
    }
    return (
      <pre className="max-h-[58vh] min-h-[20rem] overflow-auto whitespace-pre-wrap border border-[#a8a284] bg-[#fbfaf2] p-3 text-left text-[11px] leading-5 text-[#253746]" dir="ltr">
        {text ?? "فایل متنی خالی است."}
      </pre>
    );
  }

  if (mode === "audio") {
    return (
      <div className="flex min-h-[20rem] items-center justify-center border border-[#a8a284] bg-[#fbfaf2] p-4">
        <audio className="w-full" controls src={url}>
          مرورگر شما امکان پخش این فایل صوتی را ندارد.
        </audio>
      </div>
    );
  }

  if (mode === "video") {
    return (
      <div className="flex min-h-[20rem] max-h-[58vh] items-center justify-center overflow-auto border border-[#a8a284] bg-[#101820] p-3">
        <video className="max-h-[54vh] max-w-full" controls src={url}>
          مرورگر شما امکان پخش این فایل ویدیویی را ندارد.
        </video>
      </div>
    );
  }

  return (
    <div className="flex min-h-[20rem] flex-col items-center justify-center gap-2 border border-[#a8a284] bg-[#fffdf3] px-4 py-8 text-center">
      <div className="grid h-12 w-12 place-items-center border border-[#a8a284] bg-white text-2xl text-[#1f4e79]">▤</div>
      <p className="text-[12px] font-bold text-[#1f3864]">پیش‌نمایش مستقیم این فایل Native در مرورگر ممکن نیست.</p>
      <p className="max-w-xl text-[10.5px] leading-5 text-[#5c6b78]">
        فایل واقعاً در انبار اسناد ذخیره شده است؛ قالب {typeLabel(attachment)} مانند فایل‌های CAD یا قالب‌های اختصاصی، برای نمایش نیازمند نرم‌افزار تخصصی همان قالب است.
        از دکمه دریافت استفاده کنید تا فایل بدون تغییر با نرم‌افزار مربوط باز شود.
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2 text-[10px] text-[#33475b]">
        <span className="ltr border border-[#c4cbd1] bg-white px-1.5 py-0.5">{attachment.filename}</span>
        <span>{fileSize(attachment.size_bytes)}</span>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button className="btn-sm btn-primary" onClick={onDownload}>
          دریافت فایل Native
        </Button>
        <Button
          className="btn-sm"
          onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
        >
          باز کردن در پنجره جدید
        </Button>
      </div>
    </div>
  );
}
