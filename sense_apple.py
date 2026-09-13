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

from sources import MAX_AGE_DAYS, MAX_ITEMS_PER_SOURCE, SECTIONS

BASE_DIR = Path(__file__).parent
SECRETS_PATH = BASE_DIR / "secrets.json"
SEEN_PATH = BASE_DIR / "data" / "seen_links.json"
PREVIEW_PATH = BASE_DIR / "data" / "preview.html"

FETCH_TIMEOUT_DAYS_KEEP_SEEN = 30


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


def collect_report(seen: dict) -> tuple[list[dict], list[str]]:
    report_sections = []
    new_links = []
    for section in SECTIONS:
        section_items = []
        for kind, name, query in section["sources"]:
            try:
                items = fetch_source(kind, name, query)
            except Exception as exc:  # noqa: BLE001
                print(f"[경고] {name} 수집 실패: {exc}")
                continue
            for item in items:
                if item["link"] in seen:
                    continue
                section_items.append(item)
                new_links.append(item["link"])
        report_sections.append({"title": section["title"], "items": section_items})
    return report_sections, new_links


def render_html(report_sections: list[dict]) -> str:
    today = dt.date.today().isoformat()
    parts = [f"<h2>[Apple Sensing] {today} 요약</h2>"]
    for section in report_sections:
        parts.append(f"<h3>{section['title']}</h3>")
        if not section["items"]:
            parts.append("<p style='color:#888'>새 소식 없음</p>")
            continue
        parts.append("<ul>")
        for item in section["items"]:
            parts.append(
                f"<li><a href=\"{item['link']}\">{item['title']}</a> "
                f"<span style='color:#888'>- {item['source']}</span></li>"
            )
        parts.append("</ul>")
    parts.append("<h3>⑥ DRAM 공급사 영향도 분석</h3>")
    parts.append(
        "<p style='color:#888'>(수동 작성 영역) 위 헤드라인을 검토하고 "
        "수요/가격/경쟁 포지셔닝/대응 제안을 직접 정리하세요.</p>"
    )
    return "\n".join(parts)


def send_email(secrets: dict, html_body: str):
    today = dt.date.today().isoformat()
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = f"[Apple Sensing] {today} 요약"
    msg["From"] = secrets["sender_email"]
    msg["To"] = secrets.get("recipient_email", secrets["sender_email"])

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(secrets["sender_email"], secrets["app_password"])
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="메일 발송 없이 미리보기만 생성")
    args = parser.parse_args()

    seen = load_seen()
    report_sections, new_links = collect_report(seen)
    html_body = render_html(report_sections)

    total_items = sum(len(s["items"]) for s in report_sections)
    print(f"수집된 새 항목: {total_items}건")

    if args.dry_run:
        PREVIEW_PATH.parent.mkdir(exist_ok=True)
        PREVIEW_PATH.write_text(html_body, encoding="utf-8")
        print(f"드라이런 모드: 이메일을 보내지 않았습니다. 미리보기 저장 위치: {PREVIEW_PATH}")
        return

    if total_items == 0:
        print("새 항목이 없어 메일을 보내지 않았습니다.")
        return

    secrets = load_secrets()
    send_email(secrets, html_body)

    now = time.time()
    for link in new_links:
        seen[link] = now
    save_seen(seen)
    print("이메일 발송 완료.")


if __name__ == "__main__":
    main()
