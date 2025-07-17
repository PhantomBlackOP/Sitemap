import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"
OUTPUT_FILE = "sitemap.xml"

def clean_google_redirect(href):
    if "url?q=" in href:
        start = href.find("url?q=") + 6
        end = href.find("&", start)
        return href[start:end] if end != -1 else href[start:]
    return href

def get_rendered_links():
    response = requests.get(SITEMAP_URL, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    content = soup.select_one("div.nWesDd.m1rHfc")
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

def generate_sitemap(entries):
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    for entry in entries:
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = entry["url"]
        ET.SubElement(url_el, "lastmod").text = now
        ET.SubElement(url_el, "priority").text = get_priority(entry["url"])
        ET.SubElement(url_el, "changefreq").text = get_changefreq(entry["url"])

    tree = ET.ElementTree(urlset)
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

def main():
    links = get_rendered_links()
    generate_sitemap(links)

if __name__ == "__main__":
    main()
