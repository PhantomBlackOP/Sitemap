import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag
from datetime import datetime
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"
OUTPUT_FILE = "sitemap.xml"

def clean_google_redirect(url):
    if url.startswith("https://www.google.com/url?q="):
        url = url.split("&")[0].replace("https://www.google.com/url?q=", "")
    return url

def get_rendered_links():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SITEMAP_URL, wait_until="networkidle")
        content = page.locator("div.nWesDd.m1rHfc")

        if not content.count():
            raise Exception("Main content div not found")

        anchors = content.locator("a").all()
        seen = set()
        links = []

        for a in anchors:
            href = a.get_attribute("href")
            text = a.inner_text().strip()

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
                    "title": text
                })

        browser.close()
        return links

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

def main():
    links = get_rendered_links()
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    now = datetime.utcnow().isoformat() + "Z"

    for entry in links:
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = entry["url"]
        ET.SubElement(url_el, "lastmod").text = now
        ET.SubElement(url_el, "priority").text = get_priority(entry["url"])
        ET.SubElement(url_el, "changefreq").text = get_changefreq(entry["url"])

    tree = ET.ElementTree(urlset)
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
