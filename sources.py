"""Apple sensing report source definitions.

Each section maps to a list of sources. A source is either:
  - ("rss", "<name>", "<feed url>")          direct RSS/Atom feed
  - ("gnews", "<name>", "<search query>")    resolved to a Google News RSS search feed
"""

from urllib.parse import quote


def google_news_rss(query: str, lang: str = "en-US", country: str = "US") -> str:
    ceid = f"{country}:{lang.split('-')[0]}"
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={lang}&gl={country}&ceid={ceid}"


SECTIONS = [
    {
        "title": "① 핵심 헤드라인 (애플)",
        "sources": [
            ("rss", "MacRumors", "https://www.macrumors.com/macrumors.xml"),
            ("rss", "9to5Mac", "https://9to5mac.com/feed/"),
            ("rss", "The Verge - Apple", "https://www.theverge.com/apple/rss/index.xml"),
            ("rss", "Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss"),
        ],
    },
    {
        "title": "② 스펙/로드맵 루머 (애플)",
        "sources": [
            ("gnews", "Mark Gurman (Bloomberg)", 'Apple "Mark Gurman"'),
            ("gnews", "Ming-Chi Kuo", 'Apple "Ming-Chi Kuo"'),
            ("gnews", "Apple 로드맵 루머", 'Apple roadmap leak OR rumor memory OR storage'),
        ],
    },
    {
        "title": "③ 경쟁사 동향 - 메모리 공급사 (자사: 삼성전자)",
        "sources": [
            ("gnews", "SK하이닉스 [Tier1]", "SK hynix Apple OR memory OR DRAM"),
            ("gnews", "Micron [Tier1]", "Micron Technology Apple OR memory OR DRAM"),
            ("gnews", "Kioxia/WD [Tier1]", "Kioxia OR \"Western Digital\" NAND flash"),
            ("gnews", "YMTC [Watch]", "YMTC memory China"),
            ("gnews", "Nanya [Watch]", "Nanya Technology DRAM"),
        ],
    },
    {
        "title": "④ 애플 경쟁사 동향 - 디바이스",
        "sources": [
            ("gnews", "삼성 갤럭시", "Samsung Galaxy unpacked OR flagship memory"),
            ("gnews", "Huawei", "Huawei smartphone memory OR chip"),
            ("gnews", "Oppo", "Oppo smartphone flagship"),
            ("gnews", "Vivo", "Vivo smartphone flagship"),
            ("gnews", "Xiaomi", "Xiaomi smartphone flagship"),
            ("gnews", "Google Pixel", "Google Pixel memory OR chip"),
        ],
    },
    {
        "title": "⑤ 가격/수급 지표 (시장조사기관 뉴스)",
        "sources": [
            ("gnews", "TrendForce", "TrendForce DRAM OR NAND price"),
            ("gnews", "Counterpoint Research", "Counterpoint Research smartphone OR memory"),
            ("gnews", "IDC", "IDC smartphone shipment forecast"),
            ("gnews", "Gartner", "Gartner semiconductor OR memory forecast"),
            ("gnews", "Omdia", "Omdia memory OR semiconductor"),
            ("gnews", "Yole Group", "Yole Group memory OR semiconductor"),
            ("gnews", "TechInsights", "TechInsights teardown OR memory"),
            ("gnews", "DSCC", "DSCC display supply chain"),
            ("gnews", "CINNO Research", "CINNO Research memory OR display"),
            ("gnews", "Canalys", "Canalys smartphone market share"),
        ],
    },
]

MAX_ITEMS_PER_SOURCE = 4
MAX_AGE_DAYS = 1
