from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote, urlparse, parse_qs
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"

def clean_url(href):
    # Decode wrapped URLs from Google redirect
    if href.startswith("https://www.google.com/url?"):
        qs = parse_qs(urlparse(href).query)
        real = qs.get("q", [href])[0]
        return unquote(real)
    return href

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

    links = []
    for a in content.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        full_url = clean_url(href if href.startswith("http") else urljoin(BASE_URL, href))
        links.append({"url": full_url, "title": text})
    return links

def build_sitemap(links):
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    for entry in links:
        url_el = ET.SubElement(urlset, "url")
        loc = ET.SubElement(url_el, "loc")
        loc.text = entry["url"]

        lastmod = ET.SubElement(url_el, "lastmod")
        lastmod.text = now

        # Optional enhancements:
        # ET.SubElement(url_el, "priority").text = "0.5"
        # ET.SubElement(url_el, "changefreq").text = "weekly"

    return ET.ElementTree(urlset)

def main():
    links = get_rendered_links()
    print(f"✅ Cleaned and collected {len(links)} links")
    sitemap = build_sitemap(links)
    sitemap.write("sitemap.xml", encoding="utf-8", xml_declaration=True)

    # Also create an HTML preview (optional)
    with open("sitemap-preview.html", "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Sitemap Preview</title></head><body><ul>")
        for entry in links:
            f.write(f"<li><a href='{entry['url']}'>{entry['title'] or entry['url']}</a></li>\n")
        f.write("</ul></body></html>")

if __name__ == "__main__":
    main()
