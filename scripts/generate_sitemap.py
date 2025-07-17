import requests
import os
import subprocess
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag
from datetime import datetime
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"
OUTPUT_FILE = "sitemap.xml"

IMAGE_DATA = {
    f"{BASE_URL}/articles": {
        "image_loc": f"{BASE_URL}/assets/articles-banner.jpg",
        "image_title": "Anime & AI Articles"
    },
    f"{BASE_URL}/comics": {
        "image_loc": f"{BASE_URL}/assets/comics-thumb.jpg",
        "image_title": "Anime Comics & Figures"
    },
    f"{BASE_URL}/dailies": {
        "image_loc": f"{BASE_URL}/assets/dailies-header.png",
        "image_title": "Daily Fragments"
    },
}

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

        browser.close()
        print(f"✅ Extracted {len(links)} links.")
        return links

def get_priority(url):
    if url.endswith("/home") or url == BASE_URL:
        return "1.0"
    if "/dailies" in url:
        return "1.0"
    if "status" in url or "/news" in url or "/articles" in url:
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
    path = url.replace(BASE_URL, "").lstrip("/")
    if not path or url.startswith("http") and "://" not in path:
        return datetime.utcnow().isoformat() + "Z"
    try:
        result = subprocess.run(["git", "log", "-1", "--format=%cI", "--", path], capture_output=True, text=True)
        stamp = result.stdout.strip()
        return stamp + "Z" if stamp else datetime.utcnow().isoformat() + "Z"
    except Exception:
        return datetime.utcnow().isoformat() + "Z"

def main():
    links = get_rendered_links()
    if not links:
        print("⚠️ No links extracted.")
        return

    urlset = ET.Element("urlset", {
        "xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "xmlns:image": "http://www.google.com/schemas/sitemap-image/1.1"
    })

    for entry in links:
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = entry["url"]
        ET.SubElement(url_el, "lastmod").text = get_lastmod(entry["url"])
        ET.SubElement(url_el, "priority").text = get_priority(entry["url"])
        ET.SubElement(url_el, "changefreq").text = get_changefreq(entry["url"])

        if entry["url"] in IMAGE_DATA:
            image_tag = ET.SubElement(url_el, "{http://www.google.com/schemas/sitemap-image/1.1}image")
            ET.SubElement(image_tag, "{http://www.google.com/schemas/sitemap-image/1.1}loc").text = IMAGE_DATA[entry["url"]]["image_loc"]
            ET.SubElement(image_tag, "{http://www.google.com/schemas/sitemap-image/1.1}title").text = IMAGE_DATA[entry["url"]]["image_title"]

    tree = ET.ElementTree(urlset)
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)
    print(f"✅ Sitemap written to: {OUTPUT_FILE}")

    # 🔔 Notify Google to re-crawl
    ping_url = f"https://www.google.com/ping?sitemap=https://sitemap.trevorion.io/sitemap.xml"
    os.system(f"curl -s {ping_url}")
    print(f"📡 Pinged Google at: {ping_url}")

if __name__ == "__main__":
    main()
