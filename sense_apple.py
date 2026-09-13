"""Apple sensing digest: collect headlines from sources.py and email them.

Usage:
    python sense_apple.py            # collect, send email, mark items as seen
    python sense_apple.py --dry-run  # collect and print/save preview, don't send or mark seen
"""

import argparse
import calendar
import datetime as dt
import html
import json
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import feedparser
from deep_translator import MyMemoryTranslator

from sources import MAX_AGE_DAYS, MAX_ITEMS_PER_SOURCE, SECTIONS

BASE_DIR = Path(__file__).parent
SECRETS_PATH = BASE_DIR / "secrets.json"
SEEN_PATH = BASE_DIR / "data" / "seen_links.json"
PREVIEW_PATH = BASE_DIR / "data" / "preview.html"
DOCS_DIR = BASE_DIR / "docs"
PAGE_PATH = DOCS_DIR / "index.html"
TRANSLATION_CACHE_PATH = BASE_DIR / "data" / "translations.json"

FETCH_TIMEOUT_DAYS_KEEP_SEEN = 30
TRANSLATE_DELAY_SECONDS = 0.3


def load_secrets() -> dict:
    if not SECRETS_PATH.exists():
        return {}
    return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))


def require_email_secrets(secrets: dict):
    if not secrets.get("sender_email") or not secrets.get("app_password"):
        print(
            "secrets.json에 sender_email/app_password가 없습니다. secrets.example.json을 "
            "secrets.json으로 복사한 뒤 본인 Gmail 주소/앱 비밀번호를 채워주세요."
        )
        sys.exit(1)


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    return json.loads(SEEN_PATH.read_text(encoding="utf-8"))


def save_seen(seen: dict):
    SEEN_PATH.parent.mkdir(exist_ok=True)
    cutoff = time.time() - FETCH_TIMEOUT_DAYS_KEEP_SEEN * 86400
    trimmed = {url: ts for url, ts in seen.items() if ts >= cutoff}
    SEEN_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def load_translation_cache() -> dict:
    if not TRANSLATION_CACHE_PATH.exists():
        return {}
    return json.loads(TRANSLATION_CACHE_PATH.read_text(encoding="utf-8"))


def save_translation_cache(cache: dict):
    TRANSLATION_CACHE_PATH.parent.mkdir(exist_ok=True)
    TRANSLATION_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_text(text: str, translator, cache: dict) -> str | None:
    if not text:
        return None
    if text in cache:
        return cache[text]
    try:
        translated = translator.translate(text)
        time.sleep(TRANSLATE_DELAY_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] 번역 실패: {text[:40]}... ({exc})")
        translated = None
    cache[text] = translated
    return translated


def translate_sections(report_sections: list[dict], cache: dict):
    """Mutates items in place, adding item['title_ko'] / item['summary_ko']."""
    translator = MyMemoryTranslator(source="en-US", target="ko-KR")
    for section in report_sections:
        for item in section["items"]:
            item["title_ko"] = translate_text(item["title"], translator, cache)
            item["summary_ko"] = translate_text(item.get("summary", ""), translator, cache)


def entry_timestamp(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            return calendar.timegm(value)
    return None


HTML_TAG_RE = re.compile(r"<[^>]+>")
SUMMARY_MAX_CHARS = 220


def clean_summary(raw_html: str) -> str:
    text = HTML_TAG_RE.sub(" ", raw_html or "")
    text = html.unescape(text)
    text = " ".join(text.split())
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return text


def fetch_source(kind: str, name: str, url_or_query: str) -> list[dict]:
    from sources import google_news_rss

    url = google_news_rss(url_or_query) if kind == "gnews" else url_or_query
    parsed = feedparser.parse(url)
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    items = []
    for entry in parsed.entries[: MAX_ITEMS_PER_SOURCE * 3]:
        ts = entry_timestamp(entry)
        if ts is not None and ts < cutoff:
            continue
        # Google News' "summary" field is just the title re-wrapped in a link, not real
        # article content, so only direct RSS feeds get a translated summary.
        summary = clean_summary(entry.get("summary", "")) if kind == "rss" else ""
        items.append(
            {
                "source": name,
                "title": entry.get("title", "(제목 없음)"),
                "summary": summary,
                "link": entry.get("link", ""),
                "timestamp": ts,
            }
        )
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    return items


def collect_all() -> list[dict]:
    """Fetch every source once. Returns the FULL current picture (not deduped)."""
    report_sections = []
    for section in SECTIONS:
        section_items = []
        for kind, name, query in section["sources"]:
            try:
                items = fetch_source(kind, name, query)
            except Exception as exc:  # noqa: BLE001
                print(f"[경고] {name} 수집 실패: {exc}")
                continue
            section_items.extend(items)
        report_sections.append({"title": section["title"], "items": section_items})
    return report_sections


def split_new(report_sections: list[dict], seen: dict) -> tuple[list[dict], list[str]]:
    """Return a copy of report_sections containing only links not in `seen`."""
    new_sections = []
    new_links = []
    for section in report_sections:
        new_items = [item for item in section["items"] if item["link"] not in seen]
        new_links.extend(item["link"] for item in new_items)
        new_sections.append({"title": section["title"], "items": new_items})
    return new_sections, new_links


def render_section_block(section: dict) -> str:
    parts = [f"<h3>{section['title']}</h3>"]
    if not section["items"]:
        parts.append("<p style='color:#888'>소식 없음</p>")
        return "\n".join(parts)
    parts.append("<ul>")
    for item in section["items"]:
        title_ko = item.get("title_ko")
        title_ko_line = f"<br><span style='color:#555'>→ {title_ko}</span>" if title_ko else ""

        summary_ko = item.get("summary_ko")
        summary_html = ""
        if summary_ko:
            summary_html = (
                f"<div style='color:#666;font-size:0.9rem;margin-top:0.2rem'>{summary_ko}</div>"
            )

        parts.append(
            f"<li><a href=\"{item['link']}\">{item['title']}</a>"
            f"{title_ko_line}"
            f"{summary_html}"
            f" <span style='color:#888'>- {item['source']}</span></li>"
        )
    parts.append("</ul>")
    return "\n".join(parts)


IMPACT_MODEL = "claude-sonnet-5"
IMPACT_SYSTEM_PROMPT = (
    "당신은 삼성전자 메모리사업부(D램/낸드 공급사)의 Competitive Intelligence 분석가입니다. "
    "아래에 오늘 수집된 애플 및 관련 업계 뉴스 헤드라인이 섹션별로 정리되어 있습니다. "
    "이 뉴스들을 종합하여 D램 공급사 관점의 영향도 분석을 작성하세요.\n\n"
    "반드시 아래 4개 항목으로만 구성하고, 각 항목은 1~2문장으로 간결하게 한국어로 작성하세요:\n"
    "1. 수요 영향\n2. 가격 영향\n3. 경쟁 포지셔닝\n4. 대응 제안\n\n"
    "오늘 뉴스에 실제로 근거가 있는 내용만 작성하고, 관련성 있는 뉴스가 부족한 항목은 "
    "'오늘 뉴스에서는 특별한 시사점 없음'이라고 솔직하게 쓰세요. 과장하거나 추측성 정보를 "
    "지어내지 마세요.\n\n"
    "출력 형식: 마크다운 기호(#, **, -, * 등)를 절대 쓰지 말고 순수 텍스트로만 작성하세요. "
    "제목이나 인사말 없이 바로 '1. 수요 영향: ...' 형식으로 시작해서 4개 항목을 줄바꿈으로 구분하세요."
)


def build_impact_prompt(full_sections: list[dict]) -> str:
    lines = []
    for section in full_sections:
        lines.append(f"[{section['title']}]")
        if not section["items"]:
            lines.append("- (수집된 뉴스 없음)")
        for item in section["items"]:
            line = f"- {item['title']} ({item['source']})"
            if item.get("summary"):
                line += f": {item['summary']}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)


def generate_impact_analysis(full_sections: list[dict], api_key: str | None) -> str | None:
    if not api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=IMPACT_MODEL,
            max_tokens=1200,
            thinking={"type": "disabled"},
            system=IMPACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_impact_prompt(full_sections)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] 영향도 분석 생성 실패: {exc}")
        return None


def render_impact_block(analysis_text: str | None) -> str:
    if analysis_text:
        body = html.escape(analysis_text).replace("\n", "<br>")
        return f"<h3>⑥ DRAM 공급사 영향도 분석</h3>\n<div style='line-height:1.7'>{body}</div>"
    return (
        "<h3>⑥ DRAM 공급사 영향도 분석</h3>\n"
        "<p style='color:#888'>(자동 생성 실패 - 수동 작성 영역) 위 헤드라인을 검토하고 "
        "수요/가격/경쟁 포지셔닝/대응 제안을 직접 정리하세요.</p>"
    )


def render_sections(report_sections: list[dict], analysis_text: str | None = None) -> str:
    blocks = [render_section_block(section) for section in report_sections]
    blocks.append(render_impact_block(analysis_text))
    return "\n".join(blocks)


def render_email_html(new_sections: list[dict], analysis_text: str | None = None) -> str:
    today = dt.date.today().isoformat()
    return f"<h2>[Apple Sensing] {today} 신규 소식 요약</h2>\n" + render_sections(
        new_sections, analysis_text
    )


def render_page_html(full_sections: list[dict], analysis_text: str | None = None) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    blocks = [render_section_block(section) for section in full_sections]
    blocks.append(render_impact_block(analysis_text))

    rows = []
    for i in range(0, len(blocks), 2):
        pair = blocks[i : i + 2]
        cols = "\n".join(f'<div class="col">{block}</div>' for block in pair)
        rows.append(f'<div class="pair-row">{cols}</div>')
    body = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Apple Sensing Digest</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Malgun Gothic, sans-serif; max-width: 1100px;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  h3 {{ margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 0.3rem; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin-bottom: 0.4rem; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .updated {{ color: #888; font-size: 0.9rem; }}
  .pair-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; margin-top: 2rem; }}
  @media (max-width: 700px) {{
    .pair-row {{ grid-template-columns: 1fr; gap: 0; }}
  }}
</style>
</head>
<body>
  <h1>Apple Sensing Digest</h1>
  <p class="updated">마지막 업데이트: {now}</p>
  {body}
</body>
</html>
"""


def recipient_list(secrets: dict) -> list[str]:
    if "recipient_emails" in secrets:
        return secrets["recipient_emails"]
    # backward compatibility with the older single-recipient field
    return [secrets.get("recipient_email", secrets["sender_email"])]


def send_email(secrets: dict, html_body: str):
    today = dt.date.today().isoformat()
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = f"[Apple Sensing] {today} 요약"
    msg["From"] = secrets["sender_email"]
    msg["To"] = ", ".join(recipient_list(secrets))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(secrets["sender_email"], secrets["app_password"])
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="메일 발송 없이 미리보기/공개 페이지만 생성")
    args = parser.parse_args()

    secrets = load_secrets()

    seen = load_seen()
    full_sections = collect_all()

    translation_cache = load_translation_cache()
    translate_sections(full_sections, translation_cache)
    save_translation_cache(translation_cache)

    analysis_text = generate_impact_analysis(full_sections, secrets.get("anthropic_api_key"))

    new_sections, new_links = split_new(full_sections, seen)

    # 공개 페이지(docs/index.html)는 항상 "현재 시점의 전체 최신 소식"을 보여준다.
    DOCS_DIR.mkdir(exist_ok=True)
    PAGE_PATH.write_text(render_page_html(full_sections, analysis_text), encoding="utf-8")
    print(f"공개 페이지 갱신: {PAGE_PATH}")

    total_new = sum(len(s["items"]) for s in new_sections)
    print(f"수집된 새 항목(이메일 대상): {total_new}건")

    if args.dry_run:
        PREVIEW_PATH.parent.mkdir(exist_ok=True)
        PREVIEW_PATH.write_text(render_email_html(new_sections, analysis_text), encoding="utf-8")
        print(f"드라이런 모드: 이메일을 보내지 않았습니다. 미리보기 저장 위치: {PREVIEW_PATH}")
        return

    if total_new == 0:
        print("새 항목이 없어 메일을 보내지 않았습니다.")
        return

    require_email_secrets(secrets)
    send_email(secrets, render_email_html(new_sections, analysis_text))

    now = time.time()
    for link in new_links:
        seen[link] = now
    save_seen(seen)
    print("이메일 발송 완료.")


if __name__ == "__main__":
    main()
