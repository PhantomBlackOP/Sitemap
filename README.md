# 🗺️ PhantomBlackOP Sitemap Generator

[![Sitemap Build](https://github.com/PhantomBlackOP/Sitemap/actions/workflows/sitemap.yml/badge.svg)](https://github.com/PhantomBlackOP/Sitemap/actions/workflows/sitemap.yml)
![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)  
![Last Commit](https://img.shields.io/github/last-commit/PhantomBlackOP/Sitemap)  
![Repo Size](https://img.shields.io/github/repo-size/PhantomBlackOP/Sitemap)

---

## 🧠 Overview

This repo powers a dynamic `sitemap.xml` generator that:

- 🚀 Crawls a rendered sitemap page built with Google Sites
- 📅 Extracts precise last modified times from Google Sites metadata (`data-last-updated-at-time`)
- 🐦 Decodes tweet timestamps using Twitter/X Snowflake ID logic
- ✨ Gracefully assigns UTC fallback dates to non-crawlable links (like `mailto:` or `.app`)
- 🔁 Auto-generates and commits sitemap via GitHub Actions daily at **03:00 UTC**

---

## 🛠️ Tech Stack

- **Python 3.11**
- [Playwright](https://playwright.dev/python) for headless browsing and metadata scraping
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) for DOM parsing
- GitHub Actions for CI/CD automation

---

## ⚡ Usage

To run locally:

```bash
python scripts/generate_sitemap.py
# ✅ Sitemap successfully written to sitemap.xml
```
---

## 🔥 Credits

Created and curated by **Trevor Grech (@Trevorion)**  
Home of the mythkeeper's flame, daily experiments, and structured defiance.
