from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"

def get_rendered_links():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SITEMAP_URL)
        page.wait_for_load_state("networkidle")
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="sites-canvas-main-content") or soup

    links = set()
    for a in content.find_all("a", href=True):
        href = a["href"].strip()
        full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
        links.add(full_url)
    return sorted(links)

def build_sitemap(urls):
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for url in urls:
        url_el = ET.SubElement(urlset, "url")
        loc = ET.SubElement(url_el, "loc")
        loc.text = url
        lastmod = ET.SubElement(url_el, "lastmod")
        lastmod.text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return ET.ElementTree(urlset)

def main():
    links = get_rendered_links()
    print(f"✅ Found {len(links)} links.")
    sitemap = build_sitemap(links)
    sitemap.write("sitemap.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
