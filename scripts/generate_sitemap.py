import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_URL = "https://www.trevorion.io"
SITEMAP_PAGE = f"{BASE_URL}/sitemap"

def get_links():
    response = requests.get(SITEMAP_PAGE)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    urls = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/"):
            full_url = urljoin(BASE_URL, href)
            urls.add(full_url)
        elif href.startswith(BASE_URL):
            urls.add(href)
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
    links = get_links()
    sitemap = build_sitemap(links)
    sitemap.write("sitemap.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
