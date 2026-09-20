# 🗺️ Trevorion Sitemap Hub

This repository publishes:

- Human-facing sitemap UI: `https://sitemap.trevorion.io/`
- Master XML sitemap index: `https://sitemap.trevorion.io/index.xml`

## Structure

```text
index.html
index.xml

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

  news/
    YYYY-MM.xml

  articles/
    YYYY-MM.xml

  archive/
    YYYY-MM.xml

  comics/
    YYYY-MM.xml

  shop/
    YYYY-MM.xml
```

The category-level XML files are sitemap indexes. They point to monthly child XML files so the post inventories are not dumped into one enormous file.

`explore.xml` is different by design: each ISO week is itself the final report URL, so the weekly report links live directly in that file and are grouped only by year.

## Metadata

Post entries contain the normal sitemap fields plus lightweight namespaced metadata:

- canonical URL
- last modified timestamp
- actual title
- publication timestamp
- category

No post body, excerpt, tag inventory, or article content is copied into the sitemap.

The human-facing `index.html` is generated from the same WordPress inventory, so it shows real titles and publication details rather than URL slugs. Dailies therefore display their actual post titles rather than identifiers such as `20260920-1`.

## Sources

- Google Sites navigation for `www/webpage.xml`
- WordPress REST API for Zine pages, posts, categories, titles, publication dates and modification dates
- Zine page links for Profile and About section destinations
- Explore weekly report URLs derived from ISO publication weeks

Posts are included from 2025-01-01 onward. Each included WordPress post must have exactly one category. Categories outside the supplied sitemap structure are omitted.

Tag URLs are not imported. Tag Cloud remains a human section marker only.

## Automation

GitHub Actions runs the generator every day at **03:00 UTC** and also supports manual dispatch.

The generator first establishes SiteGround browser clearance in headless Chromium, then reuses the cleared browser session for WordPress REST requests. Generation is fail-closed: if retrieval or validation fails, no generated changes are committed.
