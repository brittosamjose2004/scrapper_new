# ESG & BRSR Scraper - Full Pipeline

Complete end-to-end data collection pipeline for ESG and BRSR analysis.

## Setup

### Deploy Modal App
```bash
# 1. Setup Modal (it will open browser for signin)
modal setup

# 2. Deploy the Modal app
modal deploy modal_app.py
```

## Usage

### Full Pipeline (Recommended)
The scraper runs a complete pipeline collecting all data sources:

```bash
# Basic pipeline (NSE + News + Social + Sustainability)
python scraper.py --company "Reliance"

# With BRSR Analysis
python scraper.py --company "Reliance" --modal-url "https://techintern--brsr-gemma-server-answer-question.modal.run"

# Skip slow news scraping (faster)
python scraper.py --company "Reliance" --skip-news

# Skip sustainability reports
python scraper.py --company "Reliance" --skip-sustainability
```

### Pipeline Steps

The full pipeline automatically runs:

1. **NSE India Data**
   - Annual Reports (all years available)
   - Standalone BRSR PDF/XBRL files (if available)
   
2. **News & Social Media** (can be skipped with `--skip-news`)
   - Google News RSS (50 articles)
   - Reddit posts (50 posts)
   
3. **Sustainability Reports** (can be skipped with `--skip-sustainability`)
   - TCFD Reports
   - Sustainability Reports
   - CDP Reports
   
4. **BRSR Analysis** (optional, requires `--modal-url`)
   - LLM-powered analysis of BRSR data
   - Extracts structured answers to BRSR framework questions

## Folder Structure

Data is automatically organized by source:

```
downloads/
├── annualreports.com/
│   └── {company}/
│       ├── 2024_AnnualReport.pdf
│       ├── 2023_AnnualReport.pdf
│       └── ...
└── nseindia.com/
    └── {company}/
        ├── 2024_Annual Report 2024.pdf
        ├── 2023_Annual Report 2023.pdf
        ├── BRSR/                          (standalone BRSR files if available)
        │   ├── BRSR_2025_06-Sep-2025.pdf
        │   └── BRSR_2024_06-Sep-2024.pdf
        ├── News/
        │   └── news_fulltext_YYYYMMDD.json
        ├── Social/
        │   └── social_media_consolidated_YYYYMMDD.json
        └── Sustainability/
            └── (TCFD, CDP, GRI reports)
```

**Notes:**
- Annual reports from NSE (2021+) contain embedded BRSR for top 1000 companies
- Some companies file standalone BRSR PDF/XBRL separately (saved in `BRSR/` subfolder)
- All news, social, and sustainability data saved in NSE company folder
- All data sources are processed in a single pipeline run

## Data Sources

- **NSE India** (nseindia.com) - Annual Reports + BRSR Reports
- **AnnualReports.com** - Annual Reports only (no BRSR)
- **News** - Google News RSS
- **Social** - Reddit posts
- **Sustainability** - TCFD, CDP, GRI reports via DuckDuckGo

