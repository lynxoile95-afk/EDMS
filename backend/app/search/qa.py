"""Optional, explicitly disabled-by-default grounded answer helper."""

from __future__ import annotations

import httpx

from app.core.config import settings


class QAUnavailable(RuntimeError):
    pass


async def answer_with_context(question: str, contexts: list[dict]) -> str:
    if not settings.llm_qa_enabled:
        raise QAUnavailable("پاسخ‌گویی هوشمند فعال نیست.")
    if not settings.llm_base_url or not settings.llm_model:
        raise QAUnavailable("برای پاسخ‌گویی هوشمند، نشانی و مدل LLM تنظیم نشده است.")

    context_text = "\n\n".join(
        f"[{index + 1}] {item.get('filename', '')} صفحه {item.get('page_no', '')}: {item.get('snippet', '')}"
        for index, item in enumerate(contexts[:8])
    )
    payload = {
        "model": settings.llm_model,
        "temperature": 0.1,
        "max_tokens": 500,
        "messages": [
            {
                "role": "system",
                "content": (
                    "به فارسی و فقط بر اساس قطعه‌های سند پاسخ بده. اگر پاسخ در متن نیست، "
                    "صریحاً بگو اطلاعات کافی پیدا نشد. شماره ارجاع قطعه را ذکر کن."
                ),
            },
            {
                "role": "user",
                "content": f"سؤال:\n{question}\n\nقطعه‌های مجاز:\n{context_text}",
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"]).strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise QAUnavailable("پاسخ‌گویی از سرویس LLM دریافت نشد.") from exc
