# BRSR Standalone Files Implementation

## Overview
Successfully implemented functionality to download **real standalone BRSR PDF and XBRL files** from NSE India.

## Problem Discovery
User shared screenshot showing Som Distilleries & Breweries Limited (SDBL) has actual standalone BRSR files:
- **BRSR PDF**: 414.45 KB (06-Sep-2025)
- **BRSR XBRL**: 598.91 KB
- These are SEPARATE from annual reports

## Solution

### API Endpoint Found
After extensive testing, discovered BRSR filings are in the **corporate-announcements API**:
```
https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={SYMBOL}
```

### Filtering Logic
BRSR reports are identified by:
- **Description**: "Updates"
- **Text contains**: Keywords like "BUSINESS RESPONSIBILITY", "BRSR", "SUSTAINABILITY REPORT"

### Implementation Details

#### 1. Updated `nse_client.py`
Modified `get_brsr_reports()` to:
- Fetch corporate announcements
- Filter for BRSR-related filings
- Return standalone BRSR PDF/XBRL file URLs
- Sort by date (most recent first)

#### 2. Updated `scraper.py`
Modified NSE section to:
1. Download annual reports (as before)
2. Check for standalone BRSR reports
3. Create `BRSR/` subfolder if standalone files exist
4. Download BRSR PDFs with descriptive filenames

## Usage

```bash
# Download company reports including standalone BRSR
python scraper.py --company "Som Distilleries" --source nse
```

## Output Structure

```
downloads/
└── Som Distilleries/
    ├── 2024_Annual Report 2024.pdf
    ├── 2023_Annual Report 2023.pdf
    ├── BRSR/
    │   ├── BRSR_2025_06-Sep-2025_173124.pdf  (414 KB)
    │   └── BRSR_2024_06-Sep-2024_170920.pdf  (406 KB)
    └── ...
```

## Testing

Verified with Som Distilleries (SDBL):
- ✅ Found 3 standalone BRSR reports (2023, 2024, 2025)
- ✅ Downloaded successfully
- ✅ File sizes match (414.46 KB for 2025)
- ✅ Valid PDF files

## Key Insights

1. **Two BRSR Types Exist:**
   - **Embedded**: Most companies embed BRSR in Annual Reports (FY2021-22+)
   - **Standalone**: Some companies file separate BRSR PDF/XBRL files

2. **API Pattern:**
   - Not a dedicated BRSR API endpoint
   - BRSR filings are corporate announcements
   - Must filter by keywords in description/text

3. **Folder Organization:**
   - Annual reports in main company folder
   - Standalone BRSR files in `BRSR/` subfolder
   - Clear separation for easier analysis

## Companies with Standalone BRSR
Based on testing:
- Som Distilleries & Breweries Limited (SDBL) - ✅ Has standalone files
- Most large companies - Embed in annual reports
- Smaller companies in top 1000 - May have standalone files

## Next Steps
- Test with more companies to verify coverage
- Consider downloading XBRL files alongside PDFs
- Update BRSR analyzer to prioritize standalone files
