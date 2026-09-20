#!/usr/bin/env python3
from __future__ import annotations

import calendar
import html
import json
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
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
WP_API = f"{ZINE_ROOT}/wp-json/wp/v2"
START_DATE = date(2025, 1, 1)
TIMEOUT = 60

META_NS = f"{SITEMAP_HOST}/ns/meta"
ET.register_namespace("meta", META_NS)

TOP_LEVEL_OUTPUTS = (
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

MONTHLY_SECTIONS = ("news", "articles", "archive", "comics", "shop")

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
}
PROFILE_LABEL_ONLY_SECTIONS = ("tag cloud",)

ABOUT_SECTIONS: dict[str, tuple[str, ...]] = {}
ABOUT_FIXED_SECTIONS = (
    ("welcome", f"{ZINE_ROOT}/about/"),
)
ABOUT_LABEL_ONLY_SECTIONS = ("tag cloud",)

CATEGORY_OUTPUTS = {
    "news": {"news"},
    "articles": {"article", "articles"},
    "archive": {"daily", "dailies", "daily-image", "daily-images", "archive"},
    "comics": {"comic", "comics"},
    "shop": {"advert"},
}
CATEGORY_LABELS = {
    "news": "News",
    "articles": "Articles",
    "archive": "Daily",
    "comics": "Comics",
    "shop": "Shop",
}

OWNED_HOSTS = {"trevorion.io", "www.trevorion.io", "zine.trevorion.io"}

HTTP = requests.Session()
HTTP.headers.update({
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
})


@dataclass(frozen=True)
class Item:
    loc: str
    lastmod: str | None = None
    published: datetime | None = None
    title: str | None = None
    category: str | None = None
    period: str | None = None
    tags: tuple[str, ...] | None = None


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
    query = (
        parsed.query
        if parsed.netloc.lower() == "zine.trevorion.io"
        and path.rstrip("/") == "/explore"
        else ""
    )
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, "")
    )


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
    return (
        value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if value
        else None
    )


def clean_title(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("rendered") or ""
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def is_sg_challenge(response: requests.Response) -> bool:
    return (
        response.status_code == 202
        or response.headers.get("SG-Captcha", "").lower() == "challenge"
        or "/.well-known/sgcaptcha/" in response.text[:1000]
    )


def establish_siteground_clearance() -> None:
    probe = f"{WP_API}/categories?per_page=1"
    print("ℹ️ Establishing SiteGround browser clearance…")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        try:
            page.goto(probe, wait_until="domcontentloaded", timeout=120000)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                body = page.locator("body").inner_text(timeout=5000).strip()
                if page.url.startswith(WP_API) and body[:1] in {"[", "{"}:
                    break
                page.wait_for_timeout(1000)
            else:
                raise SitemapError(
                    "SiteGround CAPTCHA did not clear automatically in Chromium within 90 seconds."
                )

            HTTP.headers["User-Agent"] = page.evaluate("navigator.userAgent")
            for cookie in context.cookies():
                HTTP.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain") or "zine.trevorion.io",
                    path=cookie.get("path") or "/",
                )
        finally:
            browser.close()

    response = HTTP.get(
        probe, timeout=TIMEOUT, headers={"Accept": "application/json"}
    )
    if is_sg_challenge(response):
        raise SitemapError(
            "SiteGround challenged the cleared browser session again when reused by the HTTP client."
        )
    if response.status_code >= 400:
        raise SitemapError(
            f"WordPress REST probe returned HTTP {response.status_code}"
        )
    try:
        response.json()
    except json.JSONDecodeError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise SitemapError(
            f"WordPress REST probe still returned non-JSON: {preview}"
        ) from exc

    print("✅ SiteGround clearance established; WordPress REST API is reachable.")


def request_json(
    url: str, params: dict[str, object]
) -> tuple[object, requests.Response]:
    response = HTTP.get(
        url,
        params=params,
        timeout=TIMEOUT,
        headers={"Accept": "application/json"},
    )
    if is_sg_challenge(response):
        raise SitemapError(
            f"SiteGround CAPTCHA reappeared while requesting {response.url}. "
            "The previous live sitemap is being kept."
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SitemapError(
            f"WordPress request failed for {response.url}: {exc}"
        ) from exc
    try:
        return response.json(), response
    except json.JSONDecodeError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise SitemapError(
            f"WordPress returned {response.headers.get('content-type', '?')} "
            f"instead of JSON for {response.url}: {preview}"
        ) from exc


def fetch_all(endpoint: str, params: dict[str, object]) -> list[dict]:
    page, rows, total_pages = 1, [], None
    while True:
        payload, response = request_json(
            f"{WP_API}/{endpoint}",
            {**params, "per_page": 100, "page": page},
        )
        if not isinstance(payload, list):
            raise SitemapError(
                f"WordPress returned invalid data for {endpoint}"
            )
        rows.extend(x for x in payload if isinstance(x, dict))
        if total_pages is None:
            raw = response.headers.get("X-WP-TotalPages", "")
            total_pages = int(raw) if raw.isdigit() else None
        if (
            total_pages is not None
            and page >= total_pages
        ) or (
            total_pages is None and len(payload) < 100
        ):
            return rows
        page += 1
        if page > 1000:
            raise SitemapError(f"Pagination limit reached for {endpoint}")


def wp_inventory() -> tuple[
    list[dict],
    list[dict],
    dict[int, dict],
    dict[int, dict],
]:
    categories = fetch_all("categories", {"hide_empty": "false"})
    category_by_id = {
        int(x["id"]): x for x in categories if "id" in x
    }
    tags = fetch_all(
        "tags",
        {
            "hide_empty": "false",
            "_fields": "id,name",
        },
    )
    tag_by_id = {
        int(x["id"]): x for x in tags if "id" in x
    }

    posts = fetch_all(
        "posts",
        {
            "status": "publish",
            "after": "2025-01-01T00:00:00Z",
            "orderby": "date",
            "order": "desc",
            "_fields": (
                "id,link,slug,title,date_gmt,modified_gmt,"
                "categories,tags,status"
            ),
        },
    )
    pages = fetch_all(
        "pages",
        {
            "status": "publish",
            "orderby": "modified",
            "order": "desc",
            "_fields": "id,link,slug,title,date_gmt,modified_gmt,status",
        },
    )

    if not posts:
        raise SitemapError(
            "WordPress returned zero published posts after 2025-01-01; "
            "refusing to overwrite the live sitemap"
        )

    print(
        f"✅ WordPress inventory: {len(posts)} posts, "
        f"{len(pages)} pages, {len(categories)} categories, "
        f"{len(tags)} tags"
    )
    return posts, pages, category_by_id, tag_by_id


def page_inventory(pages: list[dict]) -> dict[str, Item]:
    result: dict[str, Item] = {}
    for page in pages:
        loc = normalize_url(str(page.get("link") or ""))
        if not loc:
            continue
        title = clean_title(page.get("title")) or str(page.get("slug") or "")
        result[loc.rstrip("/")] = Item(
            loc=loc,
            lastmod=lastmod(dt(page.get("modified_gmt"))),
            published=dt(page.get("date_gmt")),
            title=title,
            category="Page",
        )
    return result


def html_response(url: str) -> requests.Response:
    response = HTTP.get(
        url, timeout=TIMEOUT, headers={"Accept": "text/html,*/*"}
    )
    if is_sg_challenge(response):
        raise SitemapError(
            f"SiteGround CAPTCHA reappeared while requesting {url}"
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SitemapError(f"Failed to fetch {url}: {exc}") from exc
    return response


def html_lastmod(url: str) -> str | None:
    try:
        response = html_response(url)
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


def page_item(
    label: str,
    url: str,
    pages: dict[str, Item],
    category: str = "Page",
) -> Item:
    loc = normalize_url(url)
    known = pages.get(loc.rstrip("/"))
    if known:
        return Item(
            loc=loc,
            lastmod=known.lastmod,
            published=known.published,
            title=known.title or label,
            category=category,
        )
    return Item(
        loc=loc,
        lastmod=html_lastmod(loc),
        title=label,
        category=category,
    )


def category_key(category: dict) -> str:
    value = str(
        category.get("slug") or category.get("name") or ""
    ).strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def group_posts(
    posts: list[dict],
    categories: dict[int, dict],
    tags: dict[int, dict],
) -> dict[str, list[Item]]:
    aliases = {
        alias: output
        for output, names in CATEGORY_OUTPUTS.items()
        for alias in names
    }
    groups = {name: [] for name in CATEGORY_OUTPUTS}
    skipped: defaultdict[str, int] = defaultdict(int)

    for post in posts:
        ids = post.get("categories") or []
        if len(ids) != 1:
            raise SitemapError(
                f"Post {post.get('id')} has {len(ids)} categories; "
                "exactly one is required"
            )
        category = categories.get(int(ids[0]))
        if not category:
            raise SitemapError(
                f"Post {post.get('id')} references unknown category {ids[0]}"
            )

        key = category_key(category)
        output = aliases.get(key)
        if not output:
            skipped[key or "(unnamed)"] += 1
            continue

        published = dt(post.get("date_gmt"))
        loc = normalize_url(str(post.get("link") or ""))
        title = clean_title(post.get("title"))

        tag_names = []
        for tag_id in post.get("tags") or []:
            tag = tags.get(int(tag_id))
            if not tag:
                raise SitemapError(
                    f"Post {post.get('id')} references unknown tag {tag_id}"
                )
            tag_name = clean_title(tag.get("name"))
            if tag_name:
                tag_names.append(tag_name)

        if not published or not loc or not title:
            raise SitemapError(
                f"Post {post.get('id')} is missing its canonical URL, "
                "publication date, or title"
            )

        if published.date() >= START_DATE:
            groups[output].append(
                Item(
                    loc=loc,
                    lastmod=lastmod(dt(post.get("modified_gmt"))),
                    published=published,
                    title=title,
                    category=CATEGORY_LABELS[output],
                    tags=tuple(tag_names),
                )
            )

    if skipped:
        print(
            "ℹ️ Categories outside the supplied sitemap structure were omitted: "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(skipped.items())
            )
        )

    for values in groups.values():
        values.sort(
            key=lambda x: x.published
            or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
    return groups


def scrape_owned_links(url: str) -> list[tuple[str, str]]:
    response = html_response(url)
    soup = BeautifulSoup(response.text, "html.parser")
    result = []
    for anchor in soup.find_all("a", href=True):
        loc = normalize_url(anchor.get("href", ""), url)
        if (
            not loc
            or urlparse(loc).netloc.lower() not in OWNED_HOSTS
            or urlparse(loc).path.startswith("/tag/")
        ):
            continue
        text = re.sub(
            r"\s+", " ", anchor.get_text(" ", strip=True)
        ).strip()
        result.append((text, loc))
    return result


def named_sections(
    url: str,
    sections: dict[str, tuple[str, ...]],
    label_only: tuple[str, ...] = (),
) -> list[Item]:
    links = scrape_owned_links(url)
    result, used = [], set()

    for label, terms in sections.items():
        candidates = []
        for text, loc in links:
            t, u = text.casefold(), unquote(loc).casefold()
            score = sum(
                (
                    100
                    if term.casefold() == t
                    else 40
                    if term.casefold() in t
                    else 0
                )
                + (
                    25
                    if f"/{term.casefold().replace(' ', '-')}/" in u
                    else 0
                )
                for term in terms
            )
            if score:
                candidates.append((score, len(loc), loc))

        if not candidates:
            raise SitemapError(
                f"Could not resolve '{label}' from {url}; refusing to guess"
            )

        loc = sorted(
            candidates, key=lambda x: (-x[0], x[1], x[2])
        )[0][2]
        if loc not in used:
            used.add(loc)
            result.append(
                Item(loc=loc, title=label, category="Page")
            )

    for label in label_only:
        result.append(Item(loc="", title=label, category="Section"))

    return result


def webpage_items() -> list[Item]:
    found, seen = [], set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(
                WEB_HOME, wait_until="networkidle", timeout=90000
            )
            page.wait_for_timeout(1000)

            for anchor in page.locator("a").all():
                href = anchor.get_attribute("href")
                if not href:
                    continue
                loc = normalize_url(href, WEB_HOME)
                if (
                    not loc
                    or urlparse(loc).netloc.lower() not in OWNED_HOSTS
                ):
                    continue
                if (
                    urlparse(loc).path.startswith("/tag/")
                    or re.search(
                        r"\.(?:png|jpe?g|gif|webp|svg|avif)$",
                        loc,
                        re.I,
                    )
                ):
                    continue

                if loc.rstrip("/") in {
                    WEB_ROOT.rstrip("/"),
                    WEB_HOME.rstrip("/"),
                }:
                    loc = WEB_HOME

                if loc not in seen:
                    seen.add(loc)
                    title = re.sub(
                        r"\s+", " ", anchor.inner_text().strip()
                    ) or loc
                    found.append(
                        Item(
                            loc=loc,
                            title=title,
                            category="Webpage",
                        )
                    )

            for required, title in (
                (WEB_HOME, "Home"),
                (WEB_CONTACT, "Contact"),
            ):
                if required not in seen:
                    seen.add(required)
                    found.append(
                        Item(
                            loc=required,
                            title=title,
                            category="Webpage",
                        )
                    )

            output = []
            for item in found:
                lm = None
                if item.loc.startswith(WEB_ROOT):
                    try:
                        page.goto(
                            item.loc,
                            wait_until="networkidle",
                            timeout=90000,
                        )
                        page.wait_for_timeout(400)
                        node = page.locator(
                            "[data-last-updated-at-time]"
                        ).first
                        raw = (
                            node.get_attribute(
                                "data-last-updated-at-time"
                            )
                            if node.count()
                            else None
                        )
                        if raw and raw.isdigit():
                            lm = lastmod(
                                datetime.fromtimestamp(
                                    int(raw) / 1000, tz=UTC
                                )
                            )
                    except Exception as exc:
                        print(
                            f"⚠️ No Google Sites lastmod for "
                            f"{item.loc}: {exc}"
                        )

                output.append(
                    Item(
                        loc=item.loc,
                        lastmod=lm,
                        title=item.title,
                        category=item.category,
                    )
                )

            return output
        finally:
            browser.close()


def explore_items(posts: list[dict]) -> list[Item]:
    weeks: dict[tuple[int, int], datetime] = {}

    for post in posts:
        published = dt(post.get("date_gmt"))
        modified = dt(post.get("modified_gmt")) or published
        if not published or published.date() < START_DATE:
            continue
        iso = published.isocalendar()
        key = (iso.year, iso.week)
        if modified and (
            key not in weeks or modified > weeks[key]
        ):
            weeks[key] = modified

    result = []
    for (year, week), modified in sorted(
        weeks.items(), reverse=True
    ):
        result.append(
            Item(
                loc=(
                    f"{ZINE_ROOT}/explore/"
                    f"?digest={year}-W{week:02d}&dpage=1"
                ),
                lastmod=lastmod(modified),
                title=f"Week {week:02d}",
                category="Explore",
                period=f"{year}-W{week:02d}",
            )
        )
    return result


def meta(root: ET.Element, name: str, value: str | None) -> None:
    if value:
        ET.SubElement(root, f"{{{META_NS}}}{name}").text = value


def add_url(root: ET.Element, item: Item) -> None:
    node = ET.SubElement(root, "url")
    ET.SubElement(node, "loc").text = item.loc

    if item.lastmod:
        ET.SubElement(node, "lastmod").text = item.lastmod

    meta(node, "title", item.title)
    if item.published:
        meta(node, "published", lastmod(item.published))
    meta(node, "category", item.category)
    meta(node, "period", item.period)
    if item.tags is not None:
        meta(
            node,
            "tags",
            json.dumps(list(item.tags), ensure_ascii=False),
        )


def write_urlset(
    path: str,
    items: list[Item],
    comments: bool = False,
) -> None:
    root = ET.Element(
        "urlset",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )

    for item in items:
        if comments and item.title:
            root.append(ET.Comment(item.title))
        if item.loc:
            add_url(root, item)

    ET.indent(root, space="  ")
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(
        target, encoding="utf-8", xml_declaration=True
    )

    count = sum(1 for item in items if item.loc)
    print(f"✅ {path}: {count} URLs")


def write_explore(path: str, items: list[Item]) -> None:
    root = ET.Element(
        "urlset",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
    current_year = None

    for item in items:
        year = item.period.split("-W", 1)[0] if item.period else ""
        if year and year != current_year:
            current_year = year
            root.append(ET.Comment(year))
        add_url(root, item)

    ET.indent(root, space="  ")
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(
        target, encoding="utf-8", xml_declaration=True
    )
    print(f"✅ {path}: {len(items)} weekly report URLs")


def max_lastmod(items: list[Item]) -> str | None:
    values = [item.lastmod for item in items if item.lastmod]
    return max(values) if values else None


def add_sitemap_ref(
    root: ET.Element,
    path: str,
    lm: str | None = None,
    title: str | None = None,
    section: str | None = None,
) -> None:
    node = ET.SubElement(root, "sitemap")
    ET.SubElement(node, "loc").text = f"{SITEMAP_HOST}/{path}"
    if lm:
        ET.SubElement(node, "lastmod").text = lm
    meta(node, "title", title)
    meta(node, "section", section)


def write_monthly_section(
    section: str,
    items: list[Item],
) -> list[str]:
    by_month: dict[tuple[int, int], list[Item]] = defaultdict(list)
    for item in items:
        if not item.published:
            raise SitemapError(
                f"{section} item {item.loc} has no publication date"
            )
        by_month[
            (item.published.year, item.published.month)
        ].append(item)

    generated: list[str] = []
    index_root = ET.Element(
        "sitemapindex",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
    current_year = None

    for (year, month), month_items in sorted(
        by_month.items(), reverse=True
    ):
        if year != current_year:
            current_year = year
            index_root.append(ET.Comment(str(year)))

        child = f"zine/{section}/{year}-{month:02d}.xml"
        write_urlset(child, month_items)
        generated.append(child)

        add_sitemap_ref(
            index_root,
            child,
            max_lastmod(month_items),
            f"{calendar.month_name[month]} {year}",
            CATEGORY_LABELS[section],
        )

    ET.indent(index_root, space="  ")
    target = ROOT / f"zine/{section}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(index_root).write(
        target, encoding="utf-8", xml_declaration=True
    )
    print(
        f"✅ zine/{section}.xml: "
        f"{len(by_month)} monthly child sitemaps"
    )
    return generated


def child_lastmod(path: Path) -> str | None:
    tree = ET.parse(path)
    values = [
        str(node.text)
        for node in tree.getroot().iter()
        if node.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod"
        and node.text
    ]
    return max(values) if values else None


def write_index() -> None:
    root = ET.Element(
        "sitemapindex",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
    section = None

    for output in TOP_LEVEL_OUTPUTS:
        next_section = output.split("/", 1)[0]
        if next_section != section:
            section = next_section
            root.append(ET.Comment(section))

        title = Path(output).stem.replace("-", " ").title()
        add_sitemap_ref(
            root,
            output,
            child_lastmod(ROOT / output),
            title,
            next_section,
        )

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(
        ROOT / "index.xml",
        encoding="utf-8",
        xml_declaration=True,
    )


def ui_title(item: Item) -> str:
    value = (item.title or item.loc).strip()

    if re.match(r"^https?://", value, re.I):
        parsed = urlparse(value)
        path = unquote(parsed.path).strip("/")
        if path:
            label = path.rsplit("/", 1)[-1]
            return label.replace("-", " ").replace("_", " ").title()
        if parsed.netloc.lower() in OWNED_HOSTS:
            return "Home"
        return parsed.netloc

    if value.casefold() == "trevorion.io":
        return "Trevorion.io"

    if item.category in {"Page", "Webpage", "Section"} and value.islower():
        return value.title()

    return value


def item_link_html(item: Item, show_meta: bool = True) -> str:
    title = html.escape(ui_title(item))
    loc = html.escape(item.loc, quote=True) if item.loc else ""
    link = f'<a href="{loc}">{title}</a>' if item.loc else f'<span class="label">{title}</span>'

    if not show_meta:
        return link

    details = []
    if item.published:
        stamp = lastmod(item.published) or ""
        details.append(
            f'<time datetime="{html.escape(stamp, quote=True)}">'
            f'Published {html.escape(item.published.strftime("%d %b %Y %H:%M UTC"))}'
            f"</time>"
        )
    if item.lastmod:
        details.append(
            f'<time datetime="{html.escape(item.lastmod, quote=True)}">'
            f'Updated {html.escape(item.lastmod.replace("T", " ").replace("Z", " UTC"))}'
            f"</time>"
        )
    if item.category:
        details.append(html.escape(item.category))

    if details:
        link += f'<span class="meta">{" · ".join(details)}</span>'

    if item.tags:
        tag_links = []
        for tag in item.tags:
            clean_tag = tag.lstrip("#")
            tag_url = html.escape(
                f"{ZINE_ROOT}/tag/{clean_tag.lower()}",
                quote=True,
            )
            tag_links.append(
                f'<a href="{tag_url}">#{html.escape(clean_tag)}</a>'
            )
        link += f'<span class="tags">{" ".join(tag_links)}</span>'

    return link


def item_html(item: Item, show_meta: bool = True) -> str:
    return f"<li>{item_link_html(item, show_meta)}</li>"


def details_branch(
    item: Item,
    children_html: str,
    css_class: str = "branch",
) -> str:
    return (
        f'<li class="{css_class}"><details>'
        f"<summary>{item_link_html(item)}</summary>"
        f"{children_html}"
        "</details></li>"
    )


def explore_tree_html(items: list[Item]) -> str:
    years: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        if item.period:
            years[item.period.split("-W", 1)[0]].append(item)

    parts = ['<ul class="tree nested">']
    for year in sorted(years, reverse=True):
        parts.append(
            f'<li class="branch"><details><summary>{html.escape(year)}</summary><ul>'
        )
        for item in years[year]:
            parts.append(item_html(item))
        parts.append("</ul></details></li>")
    parts.append("</ul>")
    return "".join(parts)


def monthly_tree_html(items: list[Item]) -> str:
    years: dict[int, dict[int, list[Item]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in items:
        if item.published:
            years[item.published.year][item.published.month].append(item)

    if not years:
        return '<p class="label nested-empty">No entries.</p>'

    parts = ['<ul class="tree nested">']
    for year in sorted(years, reverse=True):
        parts.append(
            f'<li class="branch"><details><summary>{year}</summary><ul>'
        )
        for month in sorted(years[year], reverse=True):
            month_items = years[year][month]
            parts.append(
                '<li class="branch"><details>'
                f'<summary>{calendar.month_name[month]} '
                f'<span class="count">({len(month_items)})</span></summary><ul>'
            )
            for item in month_items:
                parts.append(item_html(item))
            parts.append("</ul></details></li>")
        parts.append("</ul></details></li>")
    parts.append("</ul>")
    return "".join(parts)


def page_tree_html(items: list[Item]) -> str:
    return '<ul class="tree nested">' + "".join(
        item_html(item) for item in items
    ) + "</ul>"


def zine_home_map(items: list[Item]) -> dict[str, Item]:
    result = {}
    for item in items:
        path = urlparse(item.loc).path.rstrip("/") or "/"
        result[path] = item
    return result


def write_html_ui(
    web: list[Item],
    home: list[Item],
    profile: list[Item],
    explore: list[Item],
    groups: dict[str, list[Item]],
    about: list[Item],
) -> None:
    web_items = [
        item
        for item in web
        if urlparse(item.loc).netloc.lower() != "zine.trevorion.io"
    ]

    zine = zine_home_map(home)
    required = (
        "/",
        "/profile",
        "/explore",
        "/news",
        "/articles",
        "/archive",
        "/comics",
        "/shop",
        "/about",
        "/contact",
        "/search",
        "/sitemap",
        "/copyright",
    )
    missing = [path for path in required if path not in zine]
    if missing:
        raise SitemapError(
            "Human sitemap UI is missing Zine navigation entries: "
            + ", ".join(missing)
        )

    zine_rows = [
        item_html(zine["/"]),
        details_branch(zine["/profile"], page_tree_html(profile)),
        details_branch(zine["/explore"], explore_tree_html(explore)),
        details_branch(zine["/news"], monthly_tree_html(groups["news"])),
        details_branch(
            zine["/articles"],
            monthly_tree_html(groups["articles"]),
        ),
        details_branch(
            zine["/archive"],
            monthly_tree_html(groups["archive"]),
        ),
        details_branch(
            zine["/comics"],
            monthly_tree_html(groups["comics"]),
        ),
        details_branch(zine["/shop"], monthly_tree_html(groups["shop"])),
        details_branch(zine["/about"], page_tree_html(about)),
        item_html(zine["/contact"]),
        item_html(zine["/search"]),
        item_html(zine["/sitemap"]),
        item_html(zine["/copyright"]),
    ]

    body = (
        '<details class="group">'
        '<summary class="group-title">Trevorion</summary>'
        '<ul class="tree">'
        + "".join(item_html(item) for item in web_items)
        + "</ul></details>"
        '<details class="group">'
        '<summary class="group-title">Zine</summary>'
        '<ul class="tree">'
        + "".join(zine_rows)
        + "</ul></details>"
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trevorion Sitemap</title>
  <meta name="description" content="Trevorion human-readable sitemap.">
  <style>
    :root{{color-scheme:dark;--bg:#0d0f12;--panel:#151920;--line:#2b313b;--text:#eef2f7;--muted:#9ba6b2;--link:#8ec5ff}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
    main{{width:min(1180px,calc(100% - 32px));margin:42px auto 64px}}
    h1{{margin:0 0 6px;font-size:clamp(2rem,5vw,3rem)}}
    .intro{{margin:0 0 10px;color:var(--muted)}}
    .master{{display:inline-block;margin:0 0 28px}}
    .group{{margin:0 0 18px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}}
    .group-title{{font-size:1.25rem;font-weight:700}}
    details{{padding:6px 0}}
    details details{{margin-left:16px}}
    summary{{cursor:pointer;font-weight:650}}
    summary a{{position:relative;z-index:1}}
    .tree{{margin:8px 0 4px;padding-left:22px}}
    .tree.nested{{margin-top:6px}}
    .tree li{{margin:8px 0;overflow-wrap:anywhere}}
    .branch{{list-style:none;margin-left:-18px}}
    a{{color:var(--link);text-decoration:none}}
    a:hover{{text-decoration:underline}}
    .meta{{display:block;color:var(--muted);font-size:.84rem;margin-top:1px;font-weight:400}}
    .tags{{display:block;font-size:.84rem;margin-top:2px;font-weight:400}}
    .tags a{{margin-right:8px}}
    .label,.count{{color:var(--muted)}}
    .nested-empty{{margin-left:22px}}
    footer{{margin-top:28px;color:var(--muted);font-size:.9rem}}
  </style>
</head>
<body>
<main>
  <h1>Trevorion Sitemap</h1>
  <p class="intro">Human-readable sitemap for Trevorion and the Zine.</p>
  <a class="master" href="/index.xml">Master XML sitemap index</a>
  {body}
  <footer>Trevorion sitemap hub</footer>
</main>
</body>
</html>
"""
    (ROOT / "index.html").write_text(document, encoding="utf-8")
    print("✅ index.html: nested human-facing sitemap UI generated")


def validate(paths: list[str]) -> None:
    sitemap_ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    meta_title = f"{{{META_NS}}}title"
    meta_published = f"{{{META_NS}}}published"
    meta_category = f"{{{META_NS}}}category"
    meta_tags = f"{{{META_NS}}}tags"

    all_paths = ["index.xml", *TOP_LEVEL_OUTPUTS, *paths]
    if len(all_paths) != len(set(all_paths)):
        raise SitemapError("Generated sitemap path list contains duplicates")

    for rel in all_paths:
        path = ROOT / rel
        if not path.exists() or not path.stat().st_size:
            raise SitemapError(f"Missing {rel}")

        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise SitemapError(f"Invalid XML in {rel}: {exc}") from exc

        local = root.tag.rsplit("}", 1)[-1]
        if local == "sitemapindex":
            nodes = root.findall(f"{{{sitemap_ns}}}sitemap")
            locs = [
                (node.findtext(f"{{{sitemap_ns}}}loc") or "").strip()
                for node in nodes
            ]
        elif local == "urlset":
            nodes = root.findall(f"{{{sitemap_ns}}}url")
            locs = [
                (node.findtext(f"{{{sitemap_ns}}}loc") or "").strip()
                for node in nodes
            ]
        else:
            raise SitemapError(f"Unexpected root element in {rel}: {local}")

        if len(locs) != len(set(locs)):
            raise SitemapError(f"Duplicate URL inside {rel}")
        if any(not loc.startswith("https://") for loc in locs):
            raise SitemapError(f"Non-HTTPS URL inside {rel}")

        if rel.startswith("zine/") and re.search(
            r"/(?:news|articles|archive|comics|shop)/\d{4}-\d{2}\.xml$",
            rel,
        ):
            for node in nodes:
                if (
                    node.find(meta_title) is None
                    or node.find(meta_published) is None
                    or node.find(meta_category) is None
                    or node.find(meta_tags) is None
                ):
                    raise SitemapError(
                        f"Missing publication metadata inside {rel}"
                    )

    master = ET.parse(ROOT / "index.xml").getroot()
    master_count = len(
        master.findall(f"{{{sitemap_ns}}}sitemap")
    )
    if master_count != len(TOP_LEVEL_OUTPUTS):
        raise SitemapError(
            f"Master index has {master_count} child sitemaps; "
            f"expected {len(TOP_LEVEL_OUTPUTS)}"
        )


def main() -> int:
    started = time.monotonic()

    try:
        establish_siteground_clearance()
        posts, pages, categories, tags = wp_inventory()
        page_map = page_inventory(pages)
        groups = group_posts(posts, categories, tags)

        web = webpage_items()

        home = [
            page_item(label, url, page_map)
            for label, url in HOME_URLS
        ]

        profile_raw = named_sections(
            f"{ZINE_ROOT}/profile/",
            PROFILE_SECTIONS,
            PROFILE_LABEL_ONLY_SECTIONS,
        )
        profile = [
            (
                page_item(
                    item.title or "",
                    item.loc,
                    page_map,
                )
                if item.loc
                else item
            )
            for item in profile_raw
        ]

        explore = explore_items(posts)

        about = [
            page_item(label, url, page_map)
            for label, url in ABOUT_FIXED_SECTIONS
        ]
        about.extend(
            item
            if not item.loc
            else page_item(
                item.title or "",
                item.loc,
                page_map,
            )
            for item in named_sections(
                f"{ZINE_ROOT}/about/",
                ABOUT_SECTIONS,
                ABOUT_LABEL_ONLY_SECTIONS,
            )
        )

        for section in MONTHLY_SECTIONS:
            shutil.rmtree(
                ROOT / "zine" / section,
                ignore_errors=True,
            )

        write_urlset("www/webpage.xml", web, comments=True)
        write_urlset("zine/home.xml", home, comments=True)
        write_urlset("zine/profile.xml", profile, comments=True)
        write_explore("zine/explore.xml", explore)

        generated_children: list[str] = []
        for section in MONTHLY_SECTIONS:
            generated_children.extend(
                write_monthly_section(section, groups[section])
            )

        write_urlset("zine/about.xml", about, comments=True)
        write_index()
        write_html_ui(
            web,
            home,
            profile,
            explore,
            groups,
            about,
        )
        validate(generated_children)

        print(
            f"✅ Sitemap rebuild complete in "
            f"{time.monotonic() - started:.1f}s"
        )
        return 0

    except SitemapError as exc:
        print(
            f"❌ Sitemap generation aborted: {exc}",
            file=sys.stderr,
        )
        print(
            "No generated changes will be committed; "
            "the previous live sitemap remains intact.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
