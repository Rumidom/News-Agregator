import json
import os
import requests
import sys
from datetime import date, datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"}
TIMEOUT = 15
TODAY = date.today()


def is_today(date_str: str) -> bool:
    if not date_str:
        return True
    return date_str[:10] == TODAY.isoformat()


def parse_rss(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    root = ET.fromstring(resp.content)
    items = []
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        if not title:
            continue
        link = item.findtext("link", "").strip()
        pubdate = item.findtext("pubDate", "")
        date_str = ""
        if pubdate:
            try:
                dt = parsedate_to_datetime(pubdate)
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        items.append((title, date_str, link))
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:10]


# ---------------------------------------------------------------------------
# Mini HTML parser + Soup (for CNN only)
# ---------------------------------------------------------------------------

class _Node:
    def __init__(self):
        self.children = []
        self.parent = None

class TextNode(_Node):
    def __init__(self, text):
        super().__init__()
        self.text = text
    def get_text(self, strip=False):
        return self.text.strip() if strip else self.text

class TagNode(_Node):
    def __init__(self, name, attrs):
        super().__init__()
        self.name = name
        self.attrs = attrs
    def get_text(self, strip=False):
        parts = []
        for c in self.children:
            t = c.get_text(strip)
            if t:
                parts.append(t)
        return " ".join(parts) if strip else "".join(parts)
    def get(self, attr, default=None):
        return self.attrs.get(attr, default)
    @property
    def parents(self):
        cur = self.parent
        while cur:
            yield cur
            cur = cur.parent
    def find_parent(self, name):
        for p in self.parents:
            if p.name == name:
                return p
        return None
    def select(self, css):
        return _select_all(self, css)
    def select_one(self, css):
        r = self.select(css)
        return r[0] if r else None

class _HTMLBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = TagNode("__root__", {})
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = TagNode(tag.lower(), dict(attrs))
        self.stack[-1].children.append(node)
        node.parent = self.stack[-1]
        self.stack.append(node)
    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].name == tag:
                self.stack = self.stack[:i]
                break
    def handle_data(self, data):
        if data.strip():
            node = TextNode(data)
            self.stack[-1].children.append(node)
            node.parent = self.stack[-1]

class Soup:
    def __init__(self, html):
        b = _HTMLBuilder()
        b.feed(html)
        self._root = b.root
    def select(self, css):
        return _select_all(self._root, css)

def _match_simple(node, part):
    if not isinstance(node, TagNode):
        return False
    part = part.strip()
    i = 0
    while i < len(part) and (part[i].isalnum() or part[i] == "-"):
        i += 1
    tag = part[:i]
    rest = part[i:]
    if tag and node.name != tag:
        return False
    pos = 0
    while pos < len(rest):
        if rest[pos] == ".":
            end = pos + 1
            while end < len(rest) and rest[end] not in ".[":
                end += 1
            cls = rest[pos + 1 : end]
            classes = node.attrs.get("class", "")
            if not (isinstance(classes, str) and cls in classes.split()):
                return False
            pos = end
        elif rest[pos] == "[":
            end = rest.index("]", pos) + 1
            inner = rest[pos + 1 : end - 1]
            if "*=" in inner:
                attr, val = inner.split("*=", 1)
                val = val.strip('"').strip("'")
                if not (attr.strip() in node.attrs and val in node.attrs[attr.strip()]):
                    return False
            elif "=" in inner:
                attr, val = inner.split("=", 1)
                val = val.strip('"').strip("'")
                if node.attrs.get(attr.strip()) != val:
                    return False
            else:
                if inner.strip() not in node.attrs:
                    return False
            pos = end
        else:
            break
    return True

def _find_descendants(node, part):
    results = []
    for c in node.children:
        if isinstance(c, TagNode):
            if _match_simple(c, part):
                results.append(c)
            results.extend(_find_descendants(c, part))
    return results

def _select_all(root, css):
    all_results = []
    seen = set()
    for or_part in css.split(","):
        css_parts = [p for p in or_part.strip().split() if p]
        if not css_parts:
            continue
        matches = _find_descendants(root, css_parts[0])
        for p in css_parts[1:]:
            nxt = []
            for m in matches:
                nxt.extend(_find_descendants(m, p))
            matches = nxt
        for r in matches:
            i = id(r)
            if i not in seen:
                seen.add(i)
                all_results.append(r)
    return all_results


def to_ymd(raw: str) -> str:
    import calendar, time as _time
    MONTHS = {m: f"{i:02d}" for i, m in enumerate(
        ["jan","feb","mar","apr","may","jun",
         "jul","aug","sep","oct","nov","dec"], 1)}
    clean = raw.replace("Published On", "").replace("Published", "").replace("Updated", "").strip()
    parts = clean.replace(",", "").split()
    for i in range(len(parts) - 2):
        y = parts[i + 2]
        if y.isdigit() and len(y) >= 4 and y[:4].isdigit():
            day, month_name = parts[i], parts[i + 1]
            year = y[:4]
            rest = y[4:]
            if rest:
                parts = parts[:i + 3] + [rest] + parts[i + 3:]
            month_num = MONTHS.get(month_name[:3].lower())
            if month_num and day.isdigit():
                ts = calendar.timegm((int(year), int(month_num), int(day), 0, 0, 0))
                return _time.strftime("%Y-%m-%d", _time.gmtime(ts))
    if len(parts) == 3 and parts[0].isdigit() and len(parts[2]) == 4 and parts[2].isdigit():
        month_num = MONTHS.get(parts[1][:3].lower())
        if month_num:
            ts = calendar.timegm((int(parts[2]), int(month_num), int(parts[0]), 0, 0, 0))
            return _time.strftime("%Y-%m-%d", _time.gmtime(ts))
    if len(parts) == 3 and parts[1].isdigit() and len(parts[2]) == 4 and parts[2].isdigit():
        month_num = MONTHS.get(parts[0][:3].lower())
        if month_num:
            ts = calendar.timegm((int(parts[2]), int(month_num), int(parts[1]), 0, 0, 0))
            return _time.strftime("%Y-%m-%d", _time.gmtime(ts))
    return clean[:25]


def fetch_date(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=(3, 6))
        soup = Soup(resp.text)
        for t in soup.select("time, [datetime], [class*='date'], [class*='Date'], [class*='published'], [class*='Published']"):
            dt = t.get("datetime", "").strip()
            txt = t.get_text(strip=True)
            if dt:
                return dt[:10]
            if txt and any(c.isdigit() for c in txt) and len(txt) < 60:
                return to_ymd(txt)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_cnn():
    resp = requests.get("https://edition.cnn.com/world", headers=HEADERS, timeout=TIMEOUT)
    soup = Soup(resp.text)
    results = []
    for tag in soup.select("span.container__headline-text"):
        if len(results) >= 10:
            break
        title = tag.get_text(strip=True)
        if not title:
            continue
        link = ""
        parent_a = tag.find_parent("a")
        if parent_a:
            link = parent_a.get("href", "")
            if link and not link.startswith("http"):
                link = "https://edition.cnn.com" + link
        date = fetch_date(link) if link else ""
        results.append((title, date, link))
    return results


SOURCES = [
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("CNN", None),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
    ("The Guardian", "https://feeds.theguardian.com/theguardian/world/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NBC News", "https://feeds.nbcnews.com/nbcnews/public/news"),
]

all_data = {}

for name, feed_url in SOURCES:
    print(f"\n--- {name} ---", flush=True)
    try:
        if feed_url:
            items = [(t, d, l) for t, d, l in parse_rss(feed_url) if is_today(d)]
        else:
            items = [(t, d, l) for t, d, l in scrape_cnn() if is_today(d)]
        if not items:
            print("  (no headlines found)")
        all_data[name] = []
        for i, (title, date_str, link) in enumerate(items, 1):
            line = f"  {i}. {title}"
            if date_str:
                line += f"  [{date_str}]"
            print(line)
            all_data[name].append({"title": title, "date": date_str, "url": link})
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        all_data[name] = []

out_dir = os.path.join("News_Headlines", str(TODAY.year))
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, f"{TODAY.isoformat()}.json")
all_data["_meta"] = {"updated_at": datetime.now().isoformat()}
with open(out_path, "w") as f:
    json.dump(all_data, f, indent=2)
print(f"\nSaved to {out_path}")
