import argparse
import os
import requests
import sys
import time
import random
import zipfile
import io
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
    parser = argparse.ArgumentParser(description="Annual Report Scraper")
    parser.add_argument("--company", required=True, help="Company name to search for")
    parser.add_argument("--source", choices=['annualreports', 'nse', 'sustainability', 'news', 'all'], default='all', help="Source to scrape from")
    parser.add_argument("--modal-url", help="Modal App URL for BRSR Analysis (Optional). If provided, analysis runs after download.")
    
    args = parser.parse_args()
    
    company_query = args.company
    source = args.source
    
    print(f"Starting scraper for '{company_query}' from source: {source.upper()}")
    
    download_base = "downloads"
    
    # --- AnnualReports.com ---
    if source in ['annualreports', 'all']:
        print("\n--- Processing annualreports.com ---")
        client = AnnualReportsClient()
        results = client.search_company(company_query)
        
        if not results:
            print("No companies found on annualreports.com")
        else:
            # For simplicity, pick the first match or ask user? 
            # Automation favored -> pick closest or first.
            target = results[0]
            print(f"Found: {target['name']} ({target['url']})")
            
            reports = client.get_annual_reports(target['url'])
            print(f"Found {len(reports)} reports.")
            
            # Force unified folder based on user query
            # NEW: Subfolder for source
            match_folder = os.path.join(download_base, "annualreports.com", sanitize_filename(company_query))
            if not os.path.exists(match_folder):
                os.makedirs(match_folder)
            
            for report in reports:
                # Filename: Year_Description.pdf
                fname = f"{report['year']}_AnnualReport.pdf"
                # Use client headers which include User-Agent
                download_file(report['url'], match_folder, fname, headers=client.headers)

    # --- NSE India (Annual Reports & BRSR) ---
    if source in ['nse', 'all']:
        print("\n--- Processing NSE India ---")
        client = NSEClient()
        results = client.search_company(company_query)
        
        if not results:
            print("No companies found on NSE")
        else:
            target = results[0]
            print(f"Found: {target['name']} ({target['symbol']})")
            
            # Fetch Annual Reports
            reports = client.get_annual_reports(target['symbol'])
            print(f"Found {len(reports)} Annual Reports.")
            
            # Fetch BRSR specific (if any standalone)
            brsr_reports = client.get_brsr_reports(target['symbol'])
            if brsr_reports:
                print(f"Found {len(brsr_reports)} standalone BRSR Reports.")
                reports.extend(brsr_reports)
            
            # Force unified folder based on user query
            # NEW: Subfolder for source
            match_folder = os.path.join(download_base, "nseindia.com", sanitize_filename(company_query))
            if not os.path.exists(match_folder):
                os.makedirs(match_folder)
            
            for report in reports:
                # Add 'BRSR' to filename if it's a BRSR report
                desc = sanitize_filename(report['description'])
                fname = f"{report['year']}_{desc}.pdf"
                
                # Ensure .pdf extension
                if not fname.endswith('.pdf'): fname += ".pdf"
                
                # NSE needs careful headers
                download_file(report['url'], match_folder, fname, headers=client.session.headers)

    # --- Sustainability & Benchmarks (TCFD, GRI, Industry) ---
    if source in ['sustainability', 'all']:
        from search_scraper import SearchScraper
        print("\n--- Processing Secondary Reports (TCFD/GRI/Benchmarks) ---")
        searcher = SearchScraper()
        
        # Folder: Sustainability
        # Keeping these relative to company for now, or move to 'others'?
        # Let's keep them in a specific 'secondary' or just under the company root?
        # User only asked about nse/annualreports. I'll put them in 'other_sources' to be clean.
        base_company_folder = os.path.join(download_base, "other_sources", sanitize_filename(company_query))
        
        sust_folder = os.path.join(base_company_folder, "Sustainability")
        searcher.search_and_download_pdfs(company_query, "TCFD Report", sust_folder)
        searcher.search_and_download_pdfs(company_query, "Sustainability Report", sust_folder)
        searcher.search_and_download_pdfs(company_query, "CDP Report", sust_folder)
        
        # Folder: Benchmarks
        bench_folder = os.path.join(base_company_folder, "Benchmarks")
        # Try to find industry reports associated with the company
        searcher.search_and_download_pdfs(company_query, "Industry Outlook Report", bench_folder)
        searcher.search_and_download_pdfs(company_query, "Peer Comparison", bench_folder)

    # --- News & Social Media ---
    if source in ['news', 'all']:
        from news_scraper import NewsScraper
        print("\n--- Processing News & Social Media ---")
        newser = NewsScraper()
        
        base_company_folder = os.path.join(download_base, "other_sources", sanitize_filename(company_query))
        
        # News (Massive RSS)
        news_folder = os.path.join(base_company_folder, "News")
        
        # Use massive fetching strategy
        news_items = newser.fetch_massive_news(company_query, total_limit=50)
        newser.save_data(news_items, news_folder, "news_fulltext")
        
        # Social (Reddit only - others unreliable without API)
        social_folder = os.path.join(base_company_folder, "Social")
        social_items = [] 
        
        # Reddit
        social_items.extend(newser.fetch_reddit_posts(company_query, limit=50))
        
        # Note: Direct scraping of Twitter/LinkedIn suppressed due to lack of API access
        # and blocking of SERP scrapers.
        
        newser.save_data(social_items, social_folder, "social_media_consolidated")

    # --- Step 2: BRSR Analysis (Modal LLM) ---
    if args.modal_url:
        print("\n--- Starting BRSR Analysis with Modal LLM ---")
        # Inline import or use from process_reports
        try:
            from process_reports import BRSRAnalyzer, QUESTIONS_FILE
            
            # The folder naming in scraper uses sanitize_filename(target['name'])
            # We need to find the correct folder. 
            # Strategy: Search download_base for folder containing company name
            # NEW Strategy: Search in specific source folders first
            
            sanitized_query = sanitize_filename(company_query)
            target_folders = []
            
            # Check known locations
            possible_roots = ["nseindia.com", "annualreports.com"]
            
            for root in possible_roots:
                # Direct match check
                path = os.path.join(download_base, root, sanitized_query)
                if os.path.exists(path):
                    target_folders.append(os.path.join(root, sanitized_query)) # Keep relative for process_reports
                else:
                    # Fuzzy match?
                    # If sanitized doesn't match exactly what we created above (it should if logic is consistent)
                    pass

            if target_folders:
                print(f"Found download folders: {target_folders}")
                analyzer = BRSRAnalyzer(args.modal_url, QUESTIONS_FILE)
                for folder in target_folders:
                    print(f"Analyzing: {folder}")
                    analyzer.process_company(folder)
            else:
                print(f"Could not find download folder for {company_query} in nseindia.com or annualreports.com")
                
        except ImportError:
            print("Error: Could not import BRSRAnalyzer. Ensure process_reports.py is in the same directory.")
        except Exception as e:
            print(f"Analysis Failed: {e}")

if __name__ == "__main__":
    main()

