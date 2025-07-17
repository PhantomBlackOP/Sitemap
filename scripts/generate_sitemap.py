from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag, urlparse, parse_qs, unquote
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"

def clean_google_redirect(href):
    if href.startswith("https://www.google.com/url?"):
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        return unquote(qs.get("q", [href])[0])
    return href

def get_priority(url):
    if url.endswith("/home") or url == BASE_URL:
        return "1.0"
    if "/dailies" in url:
        return "1.0"
    if "status" in url:
        return "0.8"
    if "/articles" in url or "/news" in url:
        return "0.8"
    if "/2025" in url:
        return "0.6"
    return "0.5"

def get_changefreq(url):
    if url.endswith("/home") or url == BASE_URL:
        return "daily"
    if "dailies" in url or "/2025" in url:
        return "daily"
    if "sitemap" in url:
        return "daily"
    if "news" in url:
        return "weekly"
    if "articles" in url:
        return "weekly"
    if "status" in url:
        return "weekly"
    if "comics" in url:
        return "weekly"
    if "puzzles" in url:
        return "monthly"
    if "2025" in url:
        return "monthly"
    return "yearly"

def get_rendered_links():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SITEMAP_URL)
        page.wait_for_load_state("networkidle")
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="sites-canvas-main-content")
    if not content:
        raise Exception("Main content div not found")

    seen = set()
    links = []

    for a in content.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue

        href = clean_google_redirect(href)
        full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
        full_url, _ = urldefrag(full_url)
        full_url = full_url.rstrip("/")

        if full_url not in seen:
            seen.add(full_url)
            links.append({
                "url": full_url,
                "title": a.get_text(strip=True)
            })

    return links

def build_sitemap(links):
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    for entry in links:
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = entry["url"]
        ET.SubElement(url_el, "lastmod").text = now
        ET.SubElement(url_el, "priority").text = get_priority(entry["url"])
        ET.SubElement(url_el, "changefreq").text = get_changefreq(entry["url"])

    return ET.ElementTree(urlset)

def main():
    links = get_rendered_links()
    print(f"✅ Found {len(links)} unique URLs.")
    sitemap = build_sitemap(links)
    sitemap.write("sitemap.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
