import argparse
import os
import requests
import sys
import time
import random
import zipfile
import io

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from annual_reports_client import AnnualReportsClient
from nse_client import NSEClient

def is_valid_pdf(path):
    """Checks if a file starts with the %PDF signature."""
    try:
        with open(path, 'rb') as f:
            header = f.read(5)
            # Some PDFs might have a few bytes before %PDF, but usually it's at start
            return header.startswith(b'%PDF')
    except:
        return False

def download_file(url, folder, filename, headers=None, max_retries=5):
    if not os.path.exists(folder):
        os.makedirs(folder)
    
    path = os.path.join(folder, filename)
    if os.path.exists(path):
        if is_valid_pdf(path):
            print(f"  [Skipping] {filename} (already exists & valid)")
            return
        else:
            print(f"  [Re-downloading] {filename} (invalid/corrupt)")
            try:
                os.remove(path)
            except:
                pass

    print(f"  [Downloading] {filename}...")
    
    for attempt in range(max_retries):
        try:
            if headers is None:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Referer": "https://www.annualreports.com/"
                }
            
            # Add a small random delay before request to be polite
            time.sleep(random.uniform(1.0, 3.0))
            
            response = requests.get(url, stream=True, timeout=60, headers=headers)
            
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 5  # Exponential backoff: 5, 10, 20...
                print(f"  [Rate Limited] Sleeping for {wait_time}s before retry {attempt+1}/{max_retries}...")
                time.sleep(wait_time)
                continue
                
            response.raise_for_status()
            
            # Check Content-Type
            ctype = response.headers.get('Content-Type', '').lower()
            if 'application/pdf' not in ctype and 'binary' not in ctype:
                if 'text/html' in ctype:
                    print(f"  [Error] URL returned HTML instead of PDF (blocked?): {url}")
                    # Likely blocked, maybe retry with longer sleep?
                    if attempt < max_retries - 1:
                        time.sleep(10)
                        continue
                    return

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Print progress every ~1MB
                    sys.stdout.write(f"\r  [Downloading] {filename} - {downloaded / 1024 / 1024:.2f} MB")
                    sys.stdout.flush()
            print(f"\n  [Done] {filename}")
            
            # Check for ZIP signature
            is_zip = False
            try:
                is_zip = zipfile.is_zipfile(path)
            except Exception:
                pass

            if is_zip:
                print(f"  [Info] Detected ZIP file. Extracting...")
                try:
                    with zipfile.ZipFile(path, 'r') as z:
                        # Find first PDF in zip
                        pdfs = [n for n in z.namelist() if n.lower().endswith('.pdf')]
                        if pdfs:
                            print(f"  [Info] Extracting {pdfs[0]} from zip...")
                            # Extract to temporary path
                            z.extract(pdfs[0], folder)
                            extracted_path = os.path.join(folder, pdfs[0])
                            
                            # Replace the ZIP file with the extracted PDF
                            if os.path.exists(extracted_path):
                                z.close()
                                os.remove(path) # Remove the zip
                                os.rename(extracted_path, path) # Rename extracted pdf to target name
                                print(f"  [Success] Extracted and saved as {filename}")
                            else:
                                 print("  [Error] Extraction failed, file not found.")
                        else:
                            print("  [Error] No PDF found inside ZIP.")
                except Exception as e:
                    print(f"  [Error] Failed to extract ZIP: {e}")
            
            # Final Verification (Debug prints removed)
            if not is_valid_pdf(path):
                print(f"  [Error] Downloaded file is not a valid PDF header.")
                try:
                    with open(path, 'rb') as f:
                        head = f.read(200)
                    print(f"  [Debug] Header: {head}")
                    
                    # Rename to .html or .txt for inspection
                    debug_path = path + ".debug.html"
                    if os.path.exists(debug_path):
                        try: os.remove(debug_path)
                        except: pass
                    os.rename(path, debug_path)
                    print(f"  [Debug] Saved invalid file to {debug_path}")
                except Exception as e:
                    print(f"  [Debug Error] {e}")
                    if os.path.exists(path): os.remove(path)
            else:
                # Successful download, break retry loop
                return
                
        except requests.exceptions.RequestException as e:
            print(f"\n  [Network Error] {e}")
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                print(f"  Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"  [Failed] Max retries exceeded for {filename}")

        except Exception as e:
            print(f"\n  [Error] Failed to download {url}: {e}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except: 
                    pass
            break

def sanitize_filename(name):
    return "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()

def main():
    parser = argparse.ArgumentParser(description="ESG & BRSR Data Scraper - Full Pipeline")
    parser.add_argument("--company", required=True, help="Company name to search for")
    parser.add_argument("--modal-url", help="Modal App URL for BRSR Analysis (legacy). Prefer --analyze.")
    parser.add_argument("--skip-news", action="store_true", help="Skip news and social media scraping (faster)")
    parser.add_argument("--skip-sustainability", action="store_true", help="Skip sustainability reports scraping")
    parser.add_argument("--analyze", action="store_true", help="Run ESG analysis after scraping (answers ESG questions from scraped data)")
    parser.add_argument("--llm", default="gemini", help="LLM backend for ESG analysis: gemini|grok|openrouter|ollama|auto")
    parser.add_argument("--llm-chain", default=None, help="Comma-separated failover chain: gemini,grok,openrouter,ollama")
    parser.add_argument("--gemini-key", help="Google Gemini API key(s), comma-separated (or set GEMINI_API_KEY env var)")
    parser.add_argument("--grok-key", help="xAI Grok API key(s), comma-separated (or set XAI_API_KEY env var)")
    parser.add_argument("--openrouter-key", help="OpenRouter API key(s), comma-separated (or set OPENROUTER_API_KEY env var)")
    parser.add_argument("--year", type=int, default=None, help="Analyze a specific year only (e.g. --year 2023)")
    parser.add_argument("--yearly", action="store_true", help="Generate a separate ESG JSON per available annual report year")
    
    args = parser.parse_args()
    
    company_query = args.company
    
    print("=" * 80)
    print(f"🚀 FULL PIPELINE: ESG & BRSR Data Collection for '{company_query}'")
    print("=" * 80)
    print("\nPipeline Steps:")
    print("  1. AnnualReports.com - Annual Reports")
    print("  2. NSE India - Annual Reports & Standalone BRSR")
    if not args.skip_news:
        print("  3. News & Social Media - Google News, Reddit")
    if not args.skip_sustainability:
        print("  4. Sustainability Reports - TCFD, CDP, GRI")
    if args.analyze:
        print(f"  5. ESG Analysis - Answer questions using {args.llm.upper()} LLM")
    elif args.modal_url:
        print("  5. BRSR Analysis - LLM Processing (legacy)")
    print("\n" + "=" * 80)
    
    download_base = "downloads"
    sanitized_company = sanitize_filename(company_query)
    
    # ============================================================================
    # STEP 1: AnnualReports.com - Annual Reports
    # ============================================================================
    print("\n" + "=" * 80)
    print("STEP 1: AnnualReports.com - Annual Reports")
    print("=" * 80)
    
    ar_client = AnnualReportsClient()
    ar_results = ar_client.search_company(company_query)
    
    if not ar_results:
        print("❌ Company not found on AnnualReports.com")
        print("   Continuing with other data sources...")
    else:
        target = ar_results[0]
        print(f"✅ Found: {target['name']}")
        
        # Create folder: downloads/annualreports.com/{company}/
        ar_folder = os.path.join(download_base, "annualreports.com", sanitized_company)
        if not os.path.exists(ar_folder):
            os.makedirs(ar_folder)
        
        print(f"\n📄 Downloading Annual Reports...")
        reports = ar_client.get_annual_reports(target['url'])
        print(f"   Found {len(reports)} Annual Reports")
        
        for report in reports:
            fname = f"{report['year']}_AnnualReport.pdf"
            download_file(report['url'], ar_folder, fname, headers=ar_client.headers)
    
    # ============================================================================
    # STEP 2: NSE India - Annual Reports & Standalone BRSR
    # ============================================================================
    print("\n" + "=" * 80)
    print("STEP 2: NSE India - Annual Reports & BRSR")
    print("=" * 80)
    
    nse_client = NSEClient()
    nse_results = nse_client.search_company(company_query)
    
    if not nse_results:
        print("❌ Company not found on NSE India")
        print("   Continuing with other data sources...")
    else:
        target = nse_results[0]
        print(f"✅ Found: {target['name']} ({target['symbol']})")
        
        # Create folder: downloads/nseindia.com/{company}/
        nse_folder = os.path.join(download_base, "nseindia.com", sanitized_company)
        if not os.path.exists(nse_folder):
            os.makedirs(nse_folder)
        
        # 2A. Download Annual Reports
        print(f"\n📄 Downloading Annual Reports...")
        reports = nse_client.get_annual_reports(target['symbol'])
        print(f"   Found {len(reports)} Annual Reports")
        
        for report in reports:
            desc = sanitize_filename(report['description'])
            fname = f"{report['year']}_{desc}.pdf"
            if not fname.endswith('.pdf'): 
                fname += ".pdf"
            download_file(report['url'], nse_folder, fname, headers=nse_client.session.headers)
        
        # 2B. Download Standalone BRSR Reports
        print(f"\n📊 Downloading Standalone BRSR Reports...")
        brsr_reports = nse_client.get_brsr_reports(target['symbol'])
        
        if brsr_reports:
            brsr_folder = os.path.join(nse_folder, "BRSR")
            if not os.path.exists(brsr_folder):
                os.makedirs(brsr_folder)
            
            print(f"   Found {len(brsr_reports)} standalone BRSR reports")
            
            for report in brsr_reports:
                year = report['year']
                date = report.get('date', '').replace(':', '').replace(' ', '_')
                fname = f"BRSR_{year}_{date}.pdf"
                download_file(report['url'], brsr_folder, fname, headers=nse_client.session.headers)
        else:
            print(f"   ℹ️  No standalone BRSR reports found")
            print(f"   Note: Most companies embed BRSR within Annual Reports (2021+)")
    
    # ============================================================================
    # STEP 3: News & Social Media
    # ============================================================================
    if not args.skip_news:
        print("\n" + "=" * 80)
        print("STEP 3: News & Social Media")
        print("=" * 80)
        
        try:
            from news_scraper import NewsScraper
            
            news_scraper = NewsScraper()
            
            # Save in NSE company folder: downloads/nseindia.com/{company}/News
            nse_folder = os.path.join(download_base, "nseindia.com", sanitized_company)
            news_folder = os.path.join(nse_folder, "News")
            social_folder = os.path.join(nse_folder, "Social")
            
            # 3A. News (Google News RSS)
            print(f"\n📰 Fetching News Articles...")
            news_items = news_scraper.fetch_massive_news(company_query, total_limit=50)
            if news_items:
                news_scraper.save_data(news_items, news_folder, "news_fulltext")
                print(f"   ✅ Saved {len(news_items)} news articles")
            else:
                print(f"   ⚠️  No news articles found")
            
            # 3B. Social Media (Reddit)
            print(f"\n💬 Fetching Social Media Posts...")
            social_items = news_scraper.fetch_reddit_posts(company_query, limit=50)
            if social_items:
                news_scraper.save_data(social_items, social_folder, "social_media_consolidated")
                print(f"   ✅ Saved {len(social_items)} social media posts")
            else:
                print(f"   ⚠️  No social media posts found")
                
        except Exception as e:
            print(f"   ❌ Error in news/social scraping: {e}")
            print(f"   Continuing with pipeline...")
    
    # ============================================================================
    # STEP 4: Sustainability Reports (TCFD, CDP, GRI)
    # ============================================================================
    if not args.skip_sustainability:
        print("\n" + "=" * 80)
        print("STEP 4: Sustainability Reports")
        print("=" * 80)
        
        try:
            from search_scraper import SearchScraper
            
            searcher = SearchScraper()
            
            # Save in NSE company folder: downloads/nseindia.com/{company}/Sustainability
            nse_folder = os.path.join(download_base, "nseindia.com", sanitized_company)
            sust_folder = os.path.join(nse_folder, "Sustainability")
            
            print(f"\n🌱 Searching for Sustainability Reports...")
            
            # TCFD Reports
            print(f"   - TCFD Reports...")
            searcher.search_and_download_pdfs(company_query, "TCFD Report", sust_folder)
            
            # Sustainability Reports
            print(f"   - Sustainability Reports...")
            searcher.search_and_download_pdfs(company_query, "Sustainability Report", sust_folder)
            
            # CDP Reports
            print(f"   - CDP Reports...")
            searcher.search_and_download_pdfs(company_query, "CDP Report", sust_folder)
            
            print(f"   ✅ Sustainability reports search completed")
            
        except Exception as e:
            print(f"   ❌ Error in sustainability scraping: {e}")
            print(f"   Continuing with pipeline...")
    
    # ============================================================================
    # STEP 4.5: PDF → TXT Extraction (runs before ESG analysis)
    # ============================================================================
    if args.analyze:
        print("\n" + "=" * 80)
        print("STEP 4.5: Converting PDFs to TXT (handles ZIP-wrapped PDFs)")
        print("=" * 80)
        
        try:
            from pdf_to_txt import process_all_pdfs
            stats = process_all_pdfs(download_base, company_query)
            print(f"   📝 {stats['processed']} PDFs converted to TXT")
        except Exception as e:
            print(f"   ⚠️  PDF extraction had issues: {e}")
            print(f"   Continuing with ESG analysis anyway...")

    # ============================================================================
    # STEP 5: ESG Analysis (Optional - answer ESG questions from scraped data)
    # ============================================================================
    if args.analyze:
        print("\n" + "=" * 80)
        print("STEP 5: ESG Analysis — Answering Questions from Scraped Data")
        print("=" * 80)
        
        try:
            from esg_analyzer import run_esg_analysis
            
            output_file = run_esg_analysis(
                company_name=company_query,
                base_folder=download_base,
                llm_type=args.llm,
                gemini_key=args.gemini_key,
                llm_chain=args.llm_chain,
                grok_key=args.grok_key,
                openrouter_key=args.openrouter_key,
                year=args.year,
                yearly=args.yearly,
            )
            
            if output_file:
                if isinstance(output_file, list):
                    print(f"   ✅ ESG Analysis completed! {len(output_file)} yearly reports saved:")
                    for p in output_file:
                        print(f"      📄 {p}")
                else:
                    print(f"   ✅ ESG Analysis completed!")
                    print(f"   📄 Output saved to: {output_file}")
            else:
                print(f"   ⚠️  ESG Analysis had issues. Check logs above.")
                
        except Exception as e:
            print(f"   ❌ Error in ESG analysis: {e}")
            import traceback
            traceback.print_exc()
    
    elif args.modal_url:
        # Legacy Modal-based BRSR analysis (kept for backward compatibility)
        print("\n" + "=" * 80)
        print("STEP 5: BRSR Analysis with Modal LLM (Legacy)")
        print("=" * 80)
        
        try:
            from process_reports import BRSRAnalyzer, QUESTIONS_FILE
            
            nse_folder = os.path.join(download_base, "nseindia.com", sanitized_company)
            
            if not os.path.exists(nse_folder):
                print(f"   ⚠️  NSE folder not found. Skipping BRSR analysis.")
            else:
                analyzer = BRSRAnalyzer(args.modal_url, QUESTIONS_FILE)
                analyzer.process_company(sanitized_company)
                    
        except Exception as e:
            print(f"   ❌ Error in BRSR analysis: {e}")
            import traceback
            traceback.print_exc()
    
    # ============================================================================
    # PIPELINE COMPLETE
    # ============================================================================
    print("\n" + "=" * 80)
    print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 80)
    print(f"\n📁 Data organized by source:")
    print(f"   • AnnualReports.com: downloads/annualreports.com/{sanitized_company}/")
    print(f"   • NSE India (all):   downloads/nseindia.com/{sanitized_company}/")
    print(f"                        ├── Annual Reports")
    print(f"                        ├── BRSR/")
    print(f"                        ├── News/")
    print(f"                        ├── Social/")
    print(f"                        └── Sustainability/")
    print(f"\n📂 Folder Structure:")
    
    # Display folder structure for all sources
    all_folders = [
        os.path.join(download_base, "annualreports.com", sanitized_company),
        os.path.join(download_base, "nseindia.com", sanitized_company)
    ]
    
    for base_folder in all_folders:
        if os.path.exists(base_folder):
            source_name = base_folder.split(os.sep)[-2]  # Get source folder name
            print(f"\n📦 {source_name}/")
            
            for root, dirs, files in os.walk(base_folder):
                level = root.replace(base_folder, '').count(os.sep)
                indent = '  ' * (level + 1)
                folder_name = os.path.basename(root) or sanitized_company
                
                if root != base_folder:
                    print(f"{indent}📂 {folder_name}/")
                
                subindent = '  ' * (level + 2)
                # Show first 3 files per folder
                for i, file in enumerate(sorted(files)[:3]):
                    if file.endswith('.pdf'):
                        size = os.path.getsize(os.path.join(root, file)) / (1024 * 1024)
                        print(f"{subindent}📄 {file} ({size:.2f} MB)")
                    elif file.endswith('.json'):
                        print(f"{subindent}📊 {file}")
                
                if len(files) > 3:
                    print(f"{subindent}   ... and {len(files) - 3} more files")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
