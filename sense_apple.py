"""Apple sensing digest: collect headlines from sources.py and email them.

Usage:
    python sense_apple.py            # collect, send email, mark items as seen
    python sense_apple.py --dry-run  # collect and print/save preview, don't send or mark seen
"""

import argparse
import calendar
import datetime as dt
import json
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

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


def load_secrets():
    if not SECRETS_PATH.exists():
        print(
            "secrets.json이 없습니다. secrets.example.json을 secrets.json으로 복사한 뒤 "
            "본인 Gmail 주소/앱 비밀번호를 채워주세요."
        )
        sys.exit(1)
    return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))


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


def translate_sections(report_sections: list[dict], cache: dict):
    """Mutates items in place, adding item['title_ko']. Uses/updates the cache."""
    translator = MyMemoryTranslator(source="en-US", target="ko-KR")
    for section in report_sections:
        for item in section["items"]:
            title = item["title"]
            if title in cache:
                item["title_ko"] = cache[title]
                continue
            try:
                translated = translator.translate(title)
                time.sleep(TRANSLATE_DELAY_SECONDS)
            except Exception as exc:  # noqa: BLE001
                print(f"[경고] 번역 실패: {title[:40]}... ({exc})")
                translated = None
            cache[title] = translated
            item["title_ko"] = translated


def entry_timestamp(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            return calendar.timegm(value)
    return None


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
        items.append(
            {
                "source": name,
                "title": entry.get("title", "(제목 없음)"),
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
        ko_line = f"<br><span style='color:#555'>→ {title_ko}</span>" if title_ko else ""
        parts.append(
            f"<li><a href=\"{item['link']}\">{item['title']}</a>"
            f"{ko_line}"
            f" <span style='color:#888'>- {item['source']}</span></li>"
        )
    parts.append("</ul>")
    return "\n".join(parts)


def render_impact_block() -> str:
    return (
        "<h3>⑥ DRAM 공급사 영향도 분석</h3>\n"
        "<p style='color:#888'>(수동 작성 영역) 위 헤드라인을 검토하고 "
        "수요/가격/경쟁 포지셔닝/대응 제안을 직접 정리하세요.</p>"
    )


def render_sections(report_sections: list[dict]) -> str:
    blocks = [render_section_block(section) for section in report_sections]
    blocks.append(render_impact_block())
    return "\n".join(blocks)


def render_email_html(new_sections: list[dict]) -> str:
    today = dt.date.today().isoformat()
    return f"<h2>[Apple Sensing] {today} 신규 소식 요약</h2>\n" + render_sections(new_sections)


def render_page_html(full_sections: list[dict]) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    blocks = [render_section_block(section) for section in full_sections]
    blocks.append(render_impact_block())

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

    seen = load_seen()
    full_sections = collect_all()

    translation_cache = load_translation_cache()
    translate_sections(full_sections, translation_cache)
    save_translation_cache(translation_cache)

    new_sections, new_links = split_new(full_sections, seen)

    # 공개 페이지(docs/index.html)는 항상 "현재 시점의 전체 최신 소식"을 보여준다.
    DOCS_DIR.mkdir(exist_ok=True)
    PAGE_PATH.write_text(render_page_html(full_sections), encoding="utf-8")
    print(f"공개 페이지 갱신: {PAGE_PATH}")

    total_new = sum(len(s["items"]) for s in new_sections)
    print(f"수집된 새 항목(이메일 대상): {total_new}건")

    if args.dry_run:
        PREVIEW_PATH.parent.mkdir(exist_ok=True)
        PREVIEW_PATH.write_text(render_email_html(new_sections), encoding="utf-8")
        print(f"드라이런 모드: 이메일을 보내지 않았습니다. 미리보기 저장 위치: {PREVIEW_PATH}")
        return

    if total_new == 0:
        print("새 항목이 없어 메일을 보내지 않았습니다.")
        return

    secrets = load_secrets()
    send_email(secrets, render_email_html(new_sections))

    now = time.time()
    for link in new_links:
        seen[link] = now
    save_seen(seen)
    print("이메일 발송 완료.")


if __name__ == "__main__":
    main()
