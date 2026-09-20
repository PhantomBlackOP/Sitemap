# 🗺️ Trevorion Sitemap Generator

This repository publishes the curated sitemap hub directly at `https://sitemap.trevorion.io/`.

The public structure is fixed:

```text
index.html   ← served as https://sitemap.trevorion.io/

www/
  webpage.xml

zine/
  home.xml
  profile.xml
  explore.xml
  news.xml
  articles.xml
  archive.xml
  comics.xml
  shop.xml
  about.xml
```

## Sources

- `www/webpage.xml` reflects Trevorion-owned structural links on the main `www.trevorion.io` webpage, including its deliberate links into the Zine. External social/profile/image links are not imported.
- `zine/home.xml` contains the fixed Zine page set defined for the site.
- `zine/profile.xml` and `zine/about.xml` resolve only their named section destinations. The tag cloud itself is not crawled or copied into the sitemap.
- `zine/explore.xml` contains one first-page URL per weekly digest: `?digest=YYYY-Www&dpage=1`.
- `zine/news.xml`, `articles.xml`, `archive.xml`, `comics.xml`, and `shop.xml` are generated from published WordPress posts from 1 January 2025 onward. Each post is assigned through its single WordPress category.

WordPress publication time controls chronological grouping. WordPress modification time supplies `<lastmod>`.

## Automation

The master sitemap index is written directly to the site root through `index.html`. GitHub Actions runs the generator every day at **03:00 UTC** and can also be started manually with **Run workflow**. The generator validates its complete output before the workflow commits anything. If a required source cannot be read or the generated XML is invalid, the workflow fails and the previously committed sitemap remains live.

## Local run

```bash
pip install requests beautifulsoup4 playwright
playwright install chromium
python scripts/generate_sitemap.py
```
