import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin

BASE_URL = "https://www.trevorion.io"
SITEMAP_URL = f"{BASE_URL}/sitemap"

def get_all_links_from_content():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(SITEMAP_URL, headers=headers)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    content = soup.find("div", id="sites-canvas-main-content")
    if not content:
        raise Exception("Main content div not found.")

    urls = set()
    for a in content.find_all("a", href=True):
        href = a["href"].strip()
        # Leave all links intact, resolve relative URLs
        full_url = urljoin(BASE_URL, href)
        urls.add(full_url)
    return sorted(urls)

def build_sitemap(urls):
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for url in urls:
        url_element = ET.SubElement(urlset, "url")
        loc = ET.SubElement(url_element, "loc")
        loc.text = url
        lastmod = ET.SubElement(url_element, "lastmod")
        lastmod.text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return ET.ElementTree(urlset)

def main():
    links = get_all_links_from_content()
    print(f"✅ Found {len(links)} total links inside main content.")
    sitemap = build_sitemap(links)
    sitemap.write("sitemap.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
