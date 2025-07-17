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
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(SITEMAP_URL, wait_until="networkidle")

        content = page.locator("ol.n8H08c.BKnRcf").first
        if not content.count():
            print("⚠️ Structured sitemap container not found.")
            browser.close()
            return []

        anchors = content.locator("a").all()
        if not anchors:
            print("⚠️ No anchors inside sitemap container.")
            browser.close()
            return []

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
                links.append({"url": full_url, "title": text})

        print(f"✅ Extracted {len(links)} links.")
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
    if "dailies" in url or "sitemap" in url:
        return "daily"
    if "news" in url or "articles" in url or "status" in url or "comics" in url:
        return "weekly"
    if "puzzles" in url or "/2025" in url:
        return "monthly"
    return "yearly"

def get_lastmod(url):
    # Extract tweet timestamp from X (Twitter) Snowflake ID
    if "x.com" in url and "/status/" in url:
        try:
            tweet_id = url.split("/")[-1]
            timestamp_ms = int(tweet_id) >> 22
            dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
            return dt.isoformat() + "Z"
        except Exception as e:
            print(f"⚠️ Failed to parse tweet timestamp from {url}")
            return datetime.utcnow().isoformat() + "Z"
    else:
        # Fallback for non-Twitter URLs
        return datetime.utcnow().isoformat() + "Z"

def main():
    links = get_rendered_links()
    if not links:
        print("⚠️ No links to include in sitemap.")
        return

    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    for entry in links:
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = entry["url"]
        ET.SubElement(url_el, "lastmod").text = get_lastmod(entry["url"])
        ET.SubElement(url_el, "priority").text = get_priority(entry["url"])
        ET.SubElement(url_el, "changefreq").text = get_changefreq(entry["url"])

    tree = ET.ElementTree(urlset)
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)
    print(f"✅ Sitemap successfully written to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
