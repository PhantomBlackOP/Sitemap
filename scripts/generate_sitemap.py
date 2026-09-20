#!/usr/bin/env python3
from __future__ import annotations

import calendar
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITEMAP_HOST = "https://sitemap.trevorion.io"
WEB_ROOT = "https://www.trevorion.io"
WEB_HOME = f"{WEB_ROOT}/home"
WEB_CONTACT = f"{WEB_ROOT}/contact"
ZINE_ROOT = "https://zine.trevorion.io"
ZINE_SITEMAP_INDEX = f"{ZINE_ROOT}/wp-sitemap.xml"
START_DATE = date(2025, 1, 1)
TIMEOUT = 60
MAX_WORKERS = 12
USER_AGENT = "Trevorion-Sitemap/2.1 (+https://sitemap.trevorion.io/)"

OUTPUTS = (
    "www/webpage.xml",
    "zine/home.xml",
    "zine/profile.xml",
    "zine/explore.xml",
    "zine/news.xml",
    "zine/articles.xml",
    "zine/archive.xml",
    "zine/comics.xml",
    "zine/shop.xml",
    "zine/about.xml",
)

HOME_URLS = (
    ("home", f"{ZINE_ROOT}/"),
    ("profile", f"{ZINE_ROOT}/profile/"),
    ("explore", f"{ZINE_ROOT}/explore/"),
    ("news", f"{ZINE_ROOT}/news/"),
    ("articles", f"{ZINE_ROOT}/articles/"),
    ("archive", f"{ZINE_ROOT}/archive/"),
    ("comics", f"{ZINE_ROOT}/comics/"),
    ("shop", f"{ZINE_ROOT}/shop/"),
    ("about", f"{ZINE_ROOT}/about/"),
    ("contact", f"{ZINE_ROOT}/contact/"),
    ("search", f"{ZINE_ROOT}/search/"),
    ("sitemap", f"{ZINE_ROOT}/sitemap/"),
    ("copyright", f"{ZINE_ROOT}/copyright/"),
)

PROFILE_SECTIONS = {
    "daily": ("daily", "archive"),
    "articles": ("articles", "article"),
    "news": ("news",),
    "comics": ("comics", "comic"),
    "tag cloud": ("tag cloud", "tags"),
}
ABOUT_SECTIONS = {
    "welcome": ("welcome",),
    "tag cloud": ("tag cloud", "tags"),
}
CATEGORY_OUTPUTS = {
    "news": {"news"},
    "articles": {"article", "articles"},
    "archive": {"daily", "dailies", "daily-image", "daily-images", "archive"},
    "comics": {"comic", "comics"},
    "shop": {"shop"},
}
CATEGORY_ALIAS = {alias: output for output, aliases in CATEGORY_OUTPUTS.items() for alias in aliases}
OWNED_HOSTS = {"trevorion.io", "www.trevorion.io", "zine.trevorion.io"}

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/html,*/*"})


@dataclass(frozen=True)
class Item:
    loc: str
    lastmod: str | None = None
    published: datetime | None = None
    label: str | None = None


@dataclass(frozen=True)
class PostMeta:
    loc: str
    lastmod: str | None
    published: datetime
    category: str
    slug: str


class SitemapError(RuntimeError):
    pass


def normalize_url(value: str, base: str | None = None) -> str:
    value = html.unescape((value or "").strip())
    if value.startswith("https://www.google.com/url?"):
        value = unquote((parse_qs(urlparse(value).query).get("q") or [""])[0])
    if base:
        value = urljoin(base, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = parsed.query if parsed.netloc.lower() == "zine.trevorion.io" and path.rstrip("/") == "/explore" else ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def lastmod(value: datetime | None) -> str | None:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z") if value else None


def get(url: str, *, accept: str | None = None) -> requests.Response:
    headers = {"Accept": accept} if accept else None
    try:
        response = HTTP.get(url, timeout=TIMEOUT, headers=headers)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise SitemapError(f"Failed to fetch {url}: {exc}") from exc


def parse_xml(url: str) -> ET.Element:
    response = get(url, accept="application/xml,text/xml,*/*")
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        preview = response.text[:160].replace("\n", " ")
        raise SitemapError(f"Expected XML from {url}, got {response.headers.get('content-type', '?')}: {preview}") from exc


def sitemap_rows(url: str) -> list[tuple[str, str | None]]:
    root = parse_xml(url)
    rows: list[tuple[str, str | None]] = []
    for node in root.findall(".//{*}url"):
        loc_node = node.find("{*}loc")
        mod_node = node.find("{*}lastmod")
        if loc_node is None or not loc_node.text:
            continue
        loc = normalize_url(loc_node.text)
        if not loc:
            continue
        lm = lastmod(dt(mod_node.text)) if mod_node is not None and mod_node.text else None
        rows.append((loc, lm))
    return rows


def public_wp_inventory() -> tuple[list[tuple[str, str | None]], dict[str, str]]:
    index = parse_xml(ZINE_SITEMAP_INDEX)
    child_urls = [
        normalize_url(node.text or "")
        for node in index.findall(".//{*}sitemap/{*}loc")
        if node.text
    ]
    post_maps = [u for u in child_urls if re.search(r"/post-sitemap\d*\.xml$", urlparse(u).path)]
    page_maps = [u for u in child_urls if re.search(r"/page-sitemap\d*\.xml$", urlparse(u).path)]
    if not post_maps:
        raise SitemapError("No post sitemap files were found in the live WordPress sitemap index")

    posts: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for sitemap_url in post_maps:
        for loc, lm in sitemap_rows(sitemap_url):
            if loc not in seen:
                seen.add(loc)
                posts.append((loc, lm))
    if not posts:
        raise SitemapError("The live WordPress post sitemaps contained zero posts; refusing to overwrite the live sitemap")

    page_mods: dict[str, str] = {}
    for sitemap_url in page_maps:
        for loc, lm in sitemap_rows(sitemap_url):
            if lm:
                page_mods[loc.rstrip("/")] = lm

    print(f"✅ WordPress sitemap inventory: {len(posts)} posts across {len(post_maps)} post sitemap(s)")
    return posts, page_mods


def category_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def jsonld_values(value: object, key: str) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key:
                found.append(v)
            found.extend(jsonld_values(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(jsonld_values(item, key))
    return found


def parse_post_metadata(loc: str, lm: str | None) -> PostMeta:
    response = get(loc, accept="text/html,*/*")
    soup = BeautifulSoup(response.text, "html.parser")
    canonical_node = soup.find("link", rel=lambda x: x and "canonical" in x)
    canonical = normalize_url(canonical_node.get("href", "")) if canonical_node else loc
    if not canonical:
        canonical = loc
    slug = urlparse(canonical).path.strip("/").split("/")[-1]

    published: datetime | None = None
    for attrs in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"itemprop": "datePublished"},
    ):
        node = soup.find(attrs=attrs)
        if node and node.get("content"):
            published = dt(node.get("content"))
            if published:
                break

    jsonlds: list[object] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\+json", re.I)}):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            jsonlds.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if not published:
        for payload in jsonlds:
            for value in jsonld_values(payload, "datePublished"):
                published = dt(value)
                if published:
                    break
            if published:
                break

    if not published:
        for node in soup.find_all("time"):
            published = dt(node.get("datetime"))
            if published:
                break

    if not published:
        h1 = soup.find("h1")
        if h1:
            date_re = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b", re.I)
            checked = 0
            for text_node in h1.find_all_next(string=True):
                text = re.sub(r"\s+", " ", str(text_node)).strip()
                if not text:
                    continue
                checked += 1
                match = date_re.search(text)
                if match:
                    published = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %B %Y").replace(tzinfo=UTC)
                    break
                if checked >= 20:
                    break

    if not published:
        raise SitemapError(f"Could not determine publication date for {loc}")

    category_candidates: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        path = urlparse(normalize_url(anchor.get("href", ""), loc)).path
        match = re.search(r"/category/([^/]+)/?", path, re.I)
        if match:
            category_candidates.add(category_slug(unquote(match.group(1))))

    for payload in jsonlds:
        for value in jsonld_values(payload, "articleSection"):
            if isinstance(value, str):
                category_candidates.add(category_slug(value))
            elif isinstance(value, list):
                category_candidates.update(category_slug(str(x)) for x in value)

    section = soup.find("meta", attrs={"property": "article:section"})
    if section and section.get("content"):
        category_candidates.add(category_slug(section.get("content")))

    body = soup.find("body")
    if body:
        for cls in body.get("class") or []:
            match = re.fullmatch(r"category-(.+)", str(cls), re.I)
            if match:
                category_candidates.add(category_slug(match.group(1)))

    recognized = {CATEGORY_ALIAS[c] for c in category_candidates if c in CATEGORY_ALIAS}

    # Reliable legacy fallbacks where the canonical URL itself encodes the section.
    if not recognized and re.fullmatch(r"article-\d+", slug, re.I):
        recognized.add("articles")
    if not recognized and re.fullmatch(r"\d{8}-\d+", slug):
        recognized.add("archive")

    # Visible section labels are only a final fallback after structured/category links.
    if not recognized:
        text = soup.get_text(" ", strip=True).casefold()
        if "anime & ai news" in text:
            recognized.add("news")
        if re.search(r"\bcomics?\b", text) and ("next comic" in text or "previous comic" in text):
            recognized.add("comics")
        if re.search(r"\bshop\b", text) and ("next shop" in text or "previous shop" in text):
            recognized.add("shop")

    if len(recognized) != 1:
        raise SitemapError(
            f"Could not resolve exactly one sitemap category for {loc}; "
            f"recognized={sorted(recognized)}, raw={sorted(category_candidates)}"
        )

    return PostMeta(canonical, lm, published, next(iter(recognized)), slug)


def build_post_inventory(rows: list[tuple[str, str | None]]) -> list[PostMeta]:
    results: list[PostMeta] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(parse_post_metadata, loc, lm): loc for loc, lm in rows}
        done = 0
        for future in as_completed(futures):
            done += 1
            loc = futures[future]
            try:
                meta = future.result()
                if meta.published.date() >= START_DATE:
                    results.append(meta)
            except Exception as exc:
                errors.append(f"{loc}: {exc}")
            if done % 100 == 0:
                print(f"… inspected {done}/{len(rows)} post pages")

    if errors:
        preview = "\n".join(errors[:12])
        more = f"\n… and {len(errors) - 12} more" if len(errors) > 12 else ""
        raise SitemapError(f"Post metadata scan failed for {len(errors)} page(s):\n{preview}{more}")
    if not results:
        raise SitemapError("No published posts dated 2025-01-01 or later were found")
    results.sort(key=lambda x: x.published, reverse=True)
    print(f"✅ Post metadata: {len(results)} posts dated {START_DATE.isoformat()} or later")
    return results


def group_posts(posts: list[PostMeta]) -> dict[str, list[Item]]:
    groups = {name: [] for name in CATEGORY_OUTPUTS}
    for post in posts:
        groups[post.category].append(Item(post.loc, post.lastmod, post.published, post.slug))
    return groups


def html_lastmod(url: str) -> str | None:
    try:
        response = get(url, accept="text/html,*/*")
    except SitemapError:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    for attrs in (
        {"property": "article:modified_time"},
        {"name": "article:modified_time"},
        {"itemprop": "dateModified"},
    ):
        node = soup.find(attrs=attrs)
        if node and node.get("content"):
            value = lastmod(dt(node.get("content")))
            if value:
                return value
    return None


def page_lastmod(url: str, mods: dict[str, str]) -> str | None:
    return mods.get(normalize_url(url).rstrip("/")) or html_lastmod(url)


def scrape_owned_links(url: str) -> list[tuple[str, str]]:
    response = get(url, accept="text/html,*/*")
    soup = BeautifulSoup(response.text, "html.parser")
    result = []
    for anchor in soup.find_all("a", href=True):
        loc = normalize_url(anchor.get("href", ""), url)
        if not loc or urlparse(loc).netloc.lower() not in OWNED_HOSTS or urlparse(loc).path.startswith("/tag/"):
            continue
        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        result.append((text, loc))
    return result


def named_sections(url: str, sections: dict[str, tuple[str, ...]]) -> list[Item]:
    links = scrape_owned_links(url)
    result, used = [], set()
    for label, terms in sections.items():
        candidates = []
        for text, loc in links:
            t, u = text.casefold(), unquote(loc).casefold()
            score = sum(
                (100 if term.casefold() == t else 40 if term.casefold() in t else 0)
                + (25 if f"/{term.casefold().replace(' ', '-')}/" in u else 0)
                for term in terms
            )
            if score:
                candidates.append((score, len(loc), loc))
        if not candidates:
            raise SitemapError(f"Could not resolve '{label}' from {url}; refusing to guess")
        loc = sorted(candidates, key=lambda x: (-x[0], x[1], x[2]))[0][2]
        if loc not in used:
            used.add(loc)
            result.append(Item(loc, label=label))
    return result


def webpage_items() -> list[Item]:
    found, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        try:
            page.goto(WEB_HOME, wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(1000)
            for anchor in page.locator("a").all():
                href = anchor.get_attribute("href")
                if not href:
                    continue
                loc = normalize_url(href, WEB_HOME)
                if not loc or urlparse(loc).netloc.lower() not in OWNED_HOSTS:
                    continue
                if urlparse(loc).path.startswith("/tag/") or re.search(r"\.(?:png|jpe?g|gif|webp|svg|avif)$", loc, re.I):
                    continue
                if loc.rstrip("/") in {WEB_ROOT.rstrip("/"), WEB_HOME.rstrip("/")}:
                    loc = WEB_HOME
                if loc not in seen:
                    seen.add(loc)
                    found.append(Item(loc, label=re.sub(r"\s+", " ", anchor.inner_text().strip()) or None))
            for required, label in ((WEB_HOME, "home"), (WEB_CONTACT, "contact")):
                if required not in seen:
                    seen.add(required)
                    found.append(Item(required, label=label))
            output = []
            for item in found:
                lm = None
                if item.loc.startswith(WEB_ROOT):
                    try:
                        page.goto(item.loc, wait_until="networkidle", timeout=90000)
                        page.wait_for_timeout(400)
                        node = page.locator("[data-last-updated-at-time]").first
                        raw = node.get_attribute("data-last-updated-at-time") if node.count() else None
                        if raw and raw.isdigit():
                            lm = lastmod(datetime.fromtimestamp(int(raw) / 1000, tz=UTC))
                    except Exception as exc:
                        print(f"⚠️ No Google Sites lastmod for {item.loc}: {exc}")
                output.append(Item(item.loc, lm, label=item.label))
            return output
        finally:
            browser.close()


def explore_items(posts: list[PostMeta]) -> list[Item]:
    weeks: dict[tuple[int, int], datetime] = {}
    for post in posts:
        iso = post.published.isocalendar()
        modified = dt(post.lastmod) or post.published
        key = (iso.year, iso.week)
        if key not in weeks or modified > weeks[key]:
            weeks[key] = modified
    result = []
    for (year, week), modified in sorted(weeks.items(), reverse=True):
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=UTC)
        result.append(Item(
            f"{ZINE_ROOT}/explore/?digest={year}-W{week:02d}&dpage=1",
            lastmod(modified),
            monday,
            f"week {week:02d}",
        ))
    return result


def add_url(root: ET.Element, item: Item) -> None:
    node = ET.SubElement(root, "url")
    ET.SubElement(node, "loc").text = item.loc
    if item.lastmod:
        ET.SubElement(node, "lastmod").text = item.lastmod


def write_urlset(path: str, items: list[Item], grouped: str | None = None) -> None:
    root = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    year = month = None
    for item in items:
        if grouped == "month" and item.published:
            if item.published.year != year:
                year, month = item.published.year, None
                root.append(ET.Comment(str(year)))
            if item.published.month != month:
                month = item.published.month
                root.append(ET.Comment(calendar.month_name[month]))
        elif grouped == "week" and item.published:
            iso = item.published.isocalendar()
            if iso.year != year:
                year = iso.year
                root.append(ET.Comment(str(year)))
            root.append(ET.Comment(f"Week {iso.week:02d}"))
        elif item.label:
            root.append(ET.Comment(item.label))
        add_url(root, item)
    ET.indent(root, space="  ")
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)
    print(f"✅ {path}: {len(items)} URLs")


def child_lastmod(path: Path) -> str | None:
    tree = ET.parse(path)
    values = [
        x.text
        for x in tree.getroot().findall(
            "{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
            "{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod"
        )
        if x.text
    ]
    return max(values) if values else None


def write_index() -> None:
    root = ET.Element("sitemapindex", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    section = None
    for output in OUTPUTS:
        next_section = output.split("/", 1)[0]
        if next_section != section:
            section = next_section
            root.append(ET.Comment(section))
        node = ET.SubElement(root, "sitemap")
        ET.SubElement(node, "loc").text = f"{SITEMAP_HOST}/{output}"
        lm = child_lastmod(ROOT / output)
        if lm:
            ET.SubElement(node, "lastmod").text = lm
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(ROOT / "sitemap.xml", encoding="utf-8", xml_declaration=True)


def validate() -> None:
    files = [ROOT / "sitemap.xml", *(ROOT / x for x in OUTPUTS)]
    for path in files:
        if not path.exists() or not path.stat().st_size:
            raise SitemapError(f"Missing {path.relative_to(ROOT)}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise SitemapError(f"Invalid XML in {path.relative_to(ROOT)}: {exc}") from exc
        if path == ROOT / "sitemap.xml":
            count = len(root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"))
            if count != len(OUTPUTS):
                raise SitemapError(f"Master index has {count} child sitemaps; expected {len(OUTPUTS)}")
        else:
            locs = [
                x.text or ""
                for x in root.findall(
                    "{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                    "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
                )
            ]
            if len(locs) != len(set(locs)):
                raise SitemapError(f"Duplicate URL inside {path.relative_to(ROOT)}")
            if any(not x.startswith("https://") for x in locs):
                raise SitemapError(f"Non-HTTPS URL inside {path.relative_to(ROOT)}")


def main() -> int:
    started = time.monotonic()
    try:
        post_rows, page_mods = public_wp_inventory()
        posts = build_post_inventory(post_rows)
        groups = group_posts(posts)

        write_urlset("www/webpage.xml", webpage_items())
        write_urlset(
            "zine/home.xml",
            [Item(url, page_lastmod(url, page_mods), label=label) for label, url in HOME_URLS],
        )

        profile = named_sections(f"{ZINE_ROOT}/profile/", PROFILE_SECTIONS)
        write_urlset(
            "zine/profile.xml",
            [Item(x.loc, page_lastmod(x.loc, page_mods), label=x.label) for x in profile],
        )

        write_urlset("zine/explore.xml", explore_items(posts), "week")
        write_urlset("zine/news.xml", groups["news"], "month")
        write_urlset("zine/articles.xml", groups["articles"], "month")
        write_urlset("zine/archive.xml", groups["archive"], "month")
        write_urlset("zine/comics.xml", groups["comics"], "month")
        write_urlset("zine/shop.xml", groups["shop"], "month")

        about = named_sections(f"{ZINE_ROOT}/about/", ABOUT_SECTIONS)
        write_urlset(
            "zine/about.xml",
            [Item(x.loc, page_lastmod(x.loc, page_mods), label=x.label) for x in about],
        )

        write_index()
        validate()
        print(f"✅ Sitemap rebuild complete in {time.monotonic() - started:.1f}s")
        return 0
    except SitemapError as exc:
        print(f"❌ Sitemap generation aborted: {exc}", file=sys.stderr)
        print("No generated changes will be committed; the previous live sitemap remains intact.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
