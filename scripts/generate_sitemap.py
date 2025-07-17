import requests
import os
import subprocess
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

def get_rendered_links(page):
    page.goto(SITEMAP_URL, wait_until="networkidle")
    content = page.locator("ol.n8H08c.BKnRcf").first

    if not content.count():
        print("⚠️ Structured sitemap container not found.")
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

def get_banner_image(url, page):
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)

        # Target Google Sites banner container
        bg_div = page.locator("div.IFuOkc").first
        if not bg_div.count():
            return None

        style = bg_div.get_attribute("style")
        if style and "background-image" in style:
            start = style.find('url("') + 5
            end = style.find('")', start)
            src = style[start:end]

            if src:
                full_src = src if src.startswith("http") else urljoin(url, src)
                title = "Site Banner"  # fallback title; optional enhancements later
                return {"image_loc": full_src, "image_title": title}

        return None
    except Exception as e:
        print(f"⚠️ Failed to extract banner from {url}: {e}")
        return None

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        links = get_rendered_links(page)
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

            # 🖼️ Scrape banner image from the page
            image_data = get_banner_image(entry["url"], page)
            if image_data:
                image_tag = ET.SubElement(url_el, "{http://www.google.com/schemas/sitemap-image/1.1}image")
                ET.SubElement(image_tag, "{http://www.google.com/schemas/sitemap-image/1.1}loc").text = image_data["image_loc"]
                ET.SubElement(image_tag, "{http://www.google.com/schemas/sitemap-image/1.1}title").text = image_data["image_title"]

        tree = ET.ElementTree(urlset)
        tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)
        browser.close()

    print(f"✅ Sitemap written to: {OUTPUT_FILE}")
    print("📡 Google ping deprecated — rely on accurate lastmod + Search Console.")

if __name__ == "__main__":
    main()
