#!/usr/bin/env python3
from __future__ import annotations

import calendar
import html
import json
import re
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
}
PROFILE_LABEL_ONLY_SECTIONS = ("tag cloud",)

ABOUT_SECTIONS = {
    "welcome": ("welcome",),
}
ABOUT_LABEL_ONLY_SECTIONS = ("tag cloud",)
CATEGORY_OUTPUTS = {
    "news": {"news"},
    "articles": {"article", "articles"},
    "archive": {"daily", "dailies", "daily-image", "daily-images", "archive"},
    "comics": {"comic", "comics"},
    "shop": {"shop"},
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
    label: str | None = None


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


def is_sg_challenge(response: requests.Response) -> bool:
    return (
        response.status_code == 202
        or response.headers.get("SG-Captcha", "").lower() == "challenge"
        or "/.well-known/sgcaptcha/" in response.text[:1000]
    )


def establish_siteground_clearance() -> None:
    """Use a real browser to complete SiteGround's JS proof-of-work challenge.

    The resulting SiteGround cookies are copied into the requests session, so
    the REST calls use the same cleared IP/browser session rather than trying
    to bypass the challenge with a raw HTTP client.
    """
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
                url = page.url
                if url.startswith(WP_API) and body[:1] in {"[", "{"}:
                    break
                page.wait_for_timeout(1000)
            else:
                raise SitemapError(
                    "SiteGround CAPTCHA did not clear automatically in Chromium within 90 seconds. "
                    "The hosting layer is blocking the GitHub Actions runner before WordPress is reached."
                )

            user_agent = page.evaluate("navigator.userAgent")
            HTTP.headers["User-Agent"] = user_agent
            for cookie in context.cookies():
                HTTP.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain") or "zine.trevorion.io",
                    path=cookie.get("path") or "/",
                )
        finally:
            browser.close()

    response = HTTP.get(probe, timeout=TIMEOUT, headers={"Accept": "application/json"})
    if is_sg_challenge(response):
        raise SitemapError(
            "SiteGround challenged the cleared browser session again when reused by the HTTP client."
        )
    if response.status_code >= 400:
        raise SitemapError(f"WordPress REST probe returned HTTP {response.status_code}")
    try:
        response.json()
    except json.JSONDecodeError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise SitemapError(f"WordPress REST probe still returned non-JSON: {preview}") from exc

    print("✅ SiteGround clearance established; WordPress REST API is reachable.")


def request_json(url: str, params: dict[str, object]) -> tuple[object, requests.Response]:
    response = HTTP.get(url, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
    if is_sg_challenge(response):
        raise SitemapError(
            f"SiteGround CAPTCHA reappeared while requesting {response.url}. "
            "The previous live sitemap is being kept."
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SitemapError(f"WordPress request failed for {response.url}: {exc}") from exc
    try:
        return response.json(), response
    except json.JSONDecodeError as exc:
        preview = response.text[:180].replace("\n", " ")
        raise SitemapError(
            f"WordPress returned {response.headers.get('content-type', '?')} instead of JSON "
            f"for {response.url}: {preview}"
        ) from exc


def fetch_all(endpoint: str, params: dict[str, object]) -> list[dict]:
    page, rows, total_pages = 1, [], None
    while True:
        payload, response = request_json(
            f"{WP_API}/{endpoint}",
            {**params, "per_page": 100, "page": page},
        )
        if not isinstance(payload, list):
            raise SitemapError(f"WordPress returned invalid data for {endpoint}")
        rows.extend(x for x in payload if isinstance(x, dict))
        if total_pages is None:
            raw = response.headers.get("X-WP-TotalPages", "")
            total_pages = int(raw) if raw.isdigit() else None
        if (total_pages is not None and page >= total_pages) or (total_pages is None and len(payload) < 100):
            return rows
        page += 1
        if page > 1000:
            raise SitemapError(f"Pagination limit reached for {endpoint}")


def wp_inventory() -> tuple[list[dict], list[dict], dict[int, dict]]:
    categories = fetch_all("categories", {"hide_empty": "false"})
    category_by_id = {int(x["id"]): x for x in categories if "id" in x}

    posts = fetch_all("posts", {
        "status": "publish",
        "after": "2025-01-01T00:00:00Z",
        "orderby": "date",
        "order": "desc",
        "_fields": "id,link,slug,date_gmt,modified_gmt,categories,status",
    })
    pages = fetch_all("pages", {
        "status": "publish",
        "orderby": "modified",
        "order": "desc",
        "_fields": "id,link,slug,date_gmt,modified_gmt,status",
    })

    if not posts:
        raise SitemapError(
            "WordPress returned zero published posts after 2025-01-01; refusing to overwrite the live sitemap"
        )

    print(f"✅ WordPress inventory: {len(posts)} posts, {len(pages)} pages, {len(categories)} categories")
    return posts, pages, category_by_id


def page_modifications(pages: list[dict]) -> dict[str, str]:
    result = {}
    for page in pages:
        loc = normalize_url(str(page.get("link") or "")).rstrip("/")
        lm = lastmod(dt(page.get("modified_gmt")))
        if loc and lm:
            result[loc] = lm
    return result


def html_response(url: str) -> requests.Response:
    response = HTTP.get(url, timeout=TIMEOUT, headers={"Accept": "text/html,*/*"})
    if is_sg_challenge(response):
        raise SitemapError(f"SiteGround CAPTCHA reappeared while requesting {url}")
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


def page_lastmod(url: str, mods: dict[str, str]) -> str | None:
    return mods.get(normalize_url(url).rstrip("/")) or html_lastmod(url)


def category_key(category: dict) -> str:
    value = str(category.get("slug") or category.get("name") or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def group_posts(posts: list[dict], categories: dict[int, dict]) -> dict[str, list[Item]]:
    aliases = {alias: output for output, names in CATEGORY_OUTPUTS.items() for alias in names}
    groups = {name: [] for name in CATEGORY_OUTPUTS}
    skipped: defaultdict[str, int] = defaultdict(int)

    for post in posts:
        ids = post.get("categories") or []
        if len(ids) != 1:
            raise SitemapError(
                f"Post {post.get('id')} has {len(ids)} categories; exactly one is required"
            )
        category = categories.get(int(ids[0]))
        if not category:
            raise SitemapError(f"Post {post.get('id')} references unknown category {ids[0]}")

        key = category_key(category)
        output = aliases.get(key)
        if not output:
            skipped[key or "(unnamed)"] += 1
            continue

        published = dt(post.get("date_gmt"))
        loc = normalize_url(str(post.get("link") or ""))
        if not published or not loc:
            raise SitemapError(
                f"Post {post.get('id')} is missing its canonical URL or publication date"
            )
        if published.date() >= START_DATE:
            groups[output].append(
                Item(
                    loc,
                    lastmod(dt(post.get("modified_gmt"))),
                    published,
                    str(post.get("slug") or ""),
                )
            )

    if skipped:
        print(
            "ℹ️ Categories outside the supplied sitemap structure were omitted: "
            + ", ".join(f"{k}={v}" for k, v in sorted(skipped.items()))
        )

    for values in groups.values():
        values.sort(
            key=lambda x: x.published or datetime.min.replace(tzinfo=UTC),
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
        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
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
    for label in label_only:
        result.append(Item("", label=label))
    return result


def webpage_items() -> list[Item]:
    found, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
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
                if (
                    urlparse(loc).path.startswith("/tag/")
                    or re.search(r"\.(?:png|jpe?g|gif|webp|svg|avif)$", loc, re.I)
                ):
                    continue
                if loc.rstrip("/") in {WEB_ROOT.rstrip("/"), WEB_HOME.rstrip("/")}:
                    loc = WEB_HOME
                if loc not in seen:
                    seen.add(loc)
                    found.append(
                        Item(
                            loc,
                            label=re.sub(r"\s+", " ", anchor.inner_text().strip()) or None,
                        )
                    )

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
                        raw = (
                            node.get_attribute("data-last-updated-at-time")
                            if node.count()
                            else None
                        )
                        if raw and raw.isdigit():
                            lm = lastmod(
                                datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
                            )
                    except Exception as exc:
                        print(f"⚠️ No Google Sites lastmod for {item.loc}: {exc}")
                output.append(Item(item.loc, lm, label=item.label))
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
        if modified and (key not in weeks or modified > weeks[key]):
            weeks[key] = modified

    result = []
    for (year, week), modified in sorted(weeks.items(), reverse=True):
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=UTC)
        result.append(
            Item(
                f"{ZINE_ROOT}/explore/?digest={year}-W{week:02d}&dpage=1",
                lastmod(modified),
                monday,
                f"week {week:02d}",
            )
        )
    return result


def add_url(root: ET.Element, item: Item) -> None:
    node = ET.SubElement(root, "url")
    ET.SubElement(node, "loc").text = item.loc
    if item.lastmod:
        ET.SubElement(node, "lastmod").text = item.lastmod


def write_urlset(path: str, items: list[Item], grouped: str | None = None) -> None:
    root = ET.Element(
        "urlset",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
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

        if item.loc:
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
    root = ET.Element(
        "sitemapindex",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
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
    ET.ElementTree(root).write(
        ROOT / "sitemap.xml",
        encoding="utf-8",
        xml_declaration=True,
    )


def validate() -> None:
    files = [ROOT / "sitemap.xml", *(ROOT / x for x in OUTPUTS)]
    for path in files:
        if not path.exists() or not path.stat().st_size:
            raise SitemapError(f"Missing {path.relative_to(ROOT)}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise SitemapError(
                f"Invalid XML in {path.relative_to(ROOT)}: {exc}"
            ) from exc

        if path == ROOT / "sitemap.xml":
            count = len(
                root.findall(
                    "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"
                )
            )
            if count != len(OUTPUTS):
                raise SitemapError(
                    f"Master index has {count} child sitemaps; expected {len(OUTPUTS)}"
                )
        else:
            locs = [
                x.text or ""
                for x in root.findall(
                    "{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                    "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
                )
            ]
            if len(locs) != len(set(locs)):
                raise SitemapError(
                    f"Duplicate URL inside {path.relative_to(ROOT)}"
                )
            if any(not x.startswith("https://") for x in locs):
                raise SitemapError(
                    f"Non-HTTPS URL inside {path.relative_to(ROOT)}"
                )


def main() -> int:
    started = time.monotonic()
    try:
        establish_siteground_clearance()
        posts, pages, categories = wp_inventory()
        mods = page_modifications(pages)
        groups = group_posts(posts, categories)

        write_urlset("www/webpage.xml", webpage_items())
        write_urlset(
            "zine/home.xml",
            [
                Item(url, page_lastmod(url, mods), label=label)
                for label, url in HOME_URLS
            ],
        )

        profile = named_sections(
            f"{ZINE_ROOT}/profile/",
            PROFILE_SECTIONS,
            PROFILE_LABEL_ONLY_SECTIONS,
        )
        write_urlset(
            "zine/profile.xml",
            [
                Item(x.loc, page_lastmod(x.loc, mods), label=x.label)
                for x in profile
            ],
        )

        write_urlset("zine/explore.xml", explore_items(posts), "week")
        write_urlset("zine/news.xml", groups["news"], "month")
        write_urlset("zine/articles.xml", groups["articles"], "month")
        write_urlset("zine/archive.xml", groups["archive"], "month")
        write_urlset("zine/comics.xml", groups["comics"], "month")
        write_urlset("zine/shop.xml", groups["shop"], "month")

        about = named_sections(
            f"{ZINE_ROOT}/about/",
            ABOUT_SECTIONS,
            ABOUT_LABEL_ONLY_SECTIONS,
        )
        write_urlset(
            "zine/about.xml",
            [
                Item(x.loc, page_lastmod(x.loc, mods), label=x.label)
                for x in about
            ],
        )

        write_index()
        validate()
        print(
            f"✅ Sitemap rebuild complete in {time.monotonic() - started:.1f}s"
        )
        return 0

    except SitemapError as exc:
        print(f"❌ Sitemap generation aborted: {exc}", file=sys.stderr)
        print(
            "No generated changes will be committed; the previous live sitemap remains intact.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
