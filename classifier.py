import csv
import time
import argparse
import pandas as pd
import threading
import concurrent.futures
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import autopager
from parsel import Selector
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

class RateLimiter:
    """Centralized rate limiter for API calls (Queue-like behavior)"""
    def __init__(self, requests_per_minute=5):
        self.delay = 60.0 / requests_per_minute
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self.last_call = time.time()

global_rate_limiter = RateLimiter(requests_per_minute=5)

class AIJudge:
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key) if api_key else None

    def ask(self, url, detected_signals, page_snippet, branch='structural'):
        """Ask AI to judge between constrained choices based on branch.
        
        Args:
            branch: 'structural' -> next OR pageselect
                    'behavioral' -> loadmore OR scrolldown
        """
        if not self.client:
            return None
        
        # Centralized rate limit wait
        global_rate_limiter.wait()
        
        if branch == 'structural':
            prompt = f"""Address: {url}
Signals detected so far: {"; ".join(detected_signals)}

TASK: This page HAS pagination elements. Your job is to decide between EXACTLY TWO options:
- **next**: A "Next" button, arrow (>, →), or link to go to the next page sequentially.
- **pageselect**: Numbered page links (1, 2, 3...) or jump buttons (», Last) allowing direct page selection.

RULES:
- If you see NUMBERED LINKS (1, 2, 3, 4...) in a pagination bar → pageselect
- If you see only a "Next" button or single arrow without page numbers → next
- If BOTH exist, prefer next (next takes priority)
- IGNORE "Read More" buttons that link to articles, these are NOT pagination

HTML Snippet:
{page_snippet[:3000]}

Return ONLY: next OR pageselect"""
            valid_choices = ['next', 'pageselect']
        if branch == 'structural':
            prompt = f"""Address: {url}
Signals detected so far: {"; ".join(detected_signals)}

TASK: This page HAS pagination elements. Your job is to decide between EXACTLY TWO options:
- **next**: A "Next"/"Previous" button or single arrow (>, →, ›, ‹, ←) to go to the next/previous page sequentially.
- **pageselect**: Numbered page links (1, 2, 3...) or jump buttons (First, Last, », >>, «, <<) allowing direct page selection.

RULES:
- Single arrows (>, →, ›, ‹, ←) or "Next"/"Previous" text → next
- Double arrows (», >>, «, <<) or First/Last → pageselect
- If you see BOTH next AND pageselect elements → prefer next (NEXT TAKES PRIORITY)
- Numbered links (1, 2, 3) without arrows → pageselect
- IGNORE "Read More" buttons that link to individual articles.

HTML Snippet:
{page_snippet[:3000]}

Return ONLY: next OR pageselect"""
            valid_choices = ['next', 'pageselect']
        else:  # behavioral confirmation
            prompt = f"""Address: {url}
Signals detected so far: {"; ".join(detected_signals)}

TASK: This page has NO traditional pagination links. Your job is to decide between THREE options:
- **loadmore**: A button that says "Load More", "Show More", "View More", "Show All" that loads content WITHOUT navigating.
- **scrolldown**: Content loads AUTOMATICALLY when scrolling down (infinite scroll), no button click needed.
- **next**: If no clear loadmore button or scrolldown behavior, likely has hidden/traditional pagination.

RULES:
- If there's a visible button to load more content → loadmore
- If content appears automatically as you scroll → scrolldown
- If uncertain, no clear signals, or very few items → next (most common fallback)

HTML Snippet:
{page_snippet[:3000]}

Return ONLY: loadmore OR scrolldown OR next"""
            valid_choices = ['loadmore', 'scrolldown', 'next']
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    max_tokens=10,
                    temperature=0,
                    messages=[{"role": "user", "content": prompt}]
                )
                result = response.choices[0].message.content.strip().lower()
                # Validate response is one of the valid choices
                if result in valid_choices:
                    return result
                # If AI returned something else, try again
                print(f"  AI Judge returned invalid choice '{result}', retrying...")
                continue
            except Exception as e:
                if "429" in str(e):
                    wait_time = (attempt + 1) * 30
                    print(f"  AI Judge Rate Limit (429). Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                print(f"AI Judge Error: {e}")
                return None
        return None
    
    def fallback_classify(self, url):
        """
        When the page fails to load completely, use AI to make a best guess
        based only on the URL and common patterns.
        """
        if not self.client:
            return None, "No API key available"
        
        # Centralized rate limit wait
        global_rate_limiter.wait()
        
        prompt = f"""URL: {url}

This is a careers/jobs page URL that failed to load completely. Based on the URL pattern and common practices, classify the pagination type.

TASK: Choose ONE of these pagination types:
- **next**: "Next" button or arrow to go to the next page (most common for career pages)
- **pageselect**: Numbered page links (1, 2, 3...) for direct page selection
- **loadmore**: "Load More" or "Show More" button that loads content without navigation
- **scrolldown**: Infinite scroll where content loads automatically when scrolling

GUIDELINES:
- Career/job pages usually use "next" buttons (50-60% of cases)
- If URL has ?page=, &p=, /page/ → likely "next" or "pageselect"
- Modern sites with /careers/ or /jobs/ often use "loadmore" (20-30%)
- Enterprise ATS (greenhouse.io, lever.co, workday) → usually "next"
- If uncertain, prefer "next" for most career pages

Return ONLY the pagination type (next/pageselect/loadmore/scrolldown) and a brief reason in this format:
TYPE | Reason in 5-10 words

Example: next | Typical career page pattern with pagination"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                max_tokens=50,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )
            result = response.choices[0].message.content.strip()
            
            # Parse response: "TYPE | Reason"
            if '|' in result:
                bucket, reason = result.split('|', 1)
                bucket = bucket.strip().lower()
                reason = reason.strip()
            else:
                bucket = result.strip().lower()
                reason = "AI fallback classification"
            
            # Validate bucket
            valid_buckets = ['next', 'pageselect', 'loadmore', 'scrolldown']
            if bucket in valid_buckets:
                return bucket.upper(), f"AI Judge (fallback): {reason}"
            else:
                return 'NEXT', f"AI Judge fallback: {result} (defaulted to NEXT)"
                
        except Exception as e:
            print(f"  AI Judge Fallback Error: {e}")
            return None, f"AI Judge error: {str(e)[:50]}"

class PaginationClassifier:
    def __init__(self, api_key=None, headless=True):
        self.api_key = api_key
        self.ai_judge = AIJudge(api_key)
        import os
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless")  # Run headless for efficiency
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")

        driver_path = ChromeDriverManager().install()
        # Fix for webdriver-manager returning THIRD_PARTY_NOTICES or other non-executables
        if not os.access(driver_path, os.X_OK) or "THIRD_PARTY_NOTICES" in driver_path:
            parent_dir = os.path.dirname(driver_path)
            # Look for 'chromedriver' binary in the same folder
            for f in os.listdir(parent_dir):
                if f == "chromedriver" or f == "chromedriver.exe":
                    driver_path = os.path.join(parent_dir, f)
                    break
        
        # Ensure the binary is executable
        if not os.access(driver_path, os.X_OK):
            os.chmod(driver_path, 0o755)
                    
        chrome_options.set_capability("pageLoadStrategy", "eager") # Bypass asset timeouts (e.g. Suprabha)
        self.driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
        self.driver.set_page_load_timeout(30)
        self.ai_judge = AIJudge(api_key=api_key)

    def close(self):
        self.driver.quit()

    def get_page_height(self):
        return self.driver.execute_script("return document.body.scrollHeight")

    def scroll_to_bottom(self):
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2) # Wait for potential load

    def classify_url(self, url, print_callback=None):
        try:
            # Robust loading with retry - progressive wait times
            max_retries = 3
            wait_times = [3, 5, 7]  # Progressive wait: 3s -> 5s -> 7s
            timeout_limits = [10, 15, 20]  # Progressive timeout: 10s -> 15s -> 20s
            
            for attempt in range(max_retries):
                try:
                    # Set progressive page load timeout for each attempt
                    self.driver.set_page_load_timeout(timeout_limits[attempt])
                    
                    self.driver.get(url)
                    wait_time = wait_times[attempt]
                    if print_callback:
                        print_callback(f"  ⏳ Waiting {wait_time} sec for {url} to get stabilize... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time) # Progressive wait - let page stabilize
                    # Warm-up scroll to trigger lazy elements
                    self.driver.execute_script("window.scrollBy(0, 500);")
                    time.sleep(1)
                    break
                except TimeoutException:
                    if attempt == max_retries - 1: raise
                    print(f"  ⚠️  Timeout loading {url}, retrying with longer wait...")
                    time.sleep(3)
        except Exception as e:
            # Fallback for Suprabha-style hangs: Stop loading and proceed anyway
            try:
                msg = str(e).lower()
                if "timeout" in msg or "timed out" in msg:
                    try:
                        self.driver.execute_script("window.stop();")
                        print(f"  ⚠️  Warning: Timeout occurred, stopped loading and proceeding with partial page...")
                        # We don't return error here, we proceed with whatever DOM we have
                    except:
                        # If stop() fails, try to restart the driver
                        print(f"  ⚠️  Critical timeout - attempting to recover...")
                        return "error: timeout", f"Error: {e}"
                elif "dns_probe_finished_nxdomain" in msg or "unknown_error" in msg:
                    return "error: invalid_url", f"Error: {e}"
                elif "receiving message" in msg or "renderer" in msg:
                    print(f"  ⚠️  Renderer timeout - attempting recovery with partial page...")
                    # Try to proceed with whatever we have
                else:
                    return "error: page_load_failed", f"Error: {e}"
            except:
                return "error: driver_crashed", f"Error: {e}"

        detected_signals = []
        
        def extract_page_signals(driver_instance):
            signals = []
            indicators = {'next': False, 'pageselect': False, 'scrolldown': False}
            
            structural_evidence_found = False
            has_autopager_results = False
            page_source = driver_instance.page_source
            try:
                extracted_links = autopager.extract(page_source)
                if extracted_links:
                    has_autopager_results = True
            except:
                extracted_links = []
            
            found_next_symbol = False
            
            # 1.1 Autopager Analysis
            for item in extracted_links:
                try:
                    if not isinstance(item, (list, tuple)) or len(item) < 2: continue
                    link_type, link_selector = item[0], item[1]
                    
                    # Get selector/text
                    raw_text = link_selector.root.text_content() if hasattr(link_selector.root, 'text_content') else ""
                    text = raw_text.lower().strip()
                    href = link_selector.root.get('href') if hasattr(link_selector.root, 'get') else ""
                    
                    # Visibility Check: Try to find the element in DOM to verify it's displayed
                    is_visible = False
                    try:
                        # Construct a unique-ish search
                        search_xpath = f"//a[@href='{href}']" if href else f"//*[contains(text(), '{text[:20]}')]"
                        elements = driver_instance.find_elements(By.XPATH, search_xpath)
                        if any(e.is_displayed() for e in elements):
                            is_visible = True
                    except: 
                        is_visible = False # Fallback to hidden if check fails

                    if not is_visible: continue

                    if link_type == 'PAGE':
                        indicators['pageselect'] = True
                        signals.append(f"Autopager: Found PAGE link '{text}'")
                        structural_evidence_found = True
                    elif link_type == 'NEXT':
                        # Mark as NEXT - takes priority over pageselect
                        indicators['next'] = True
                        found_next_symbol = True
                        signals.append(f"Autopager: Found NEXT link '{text}'")
                        structural_evidence_found = True
                except: continue

            # 1.2 Keyword Search (Next/Arrow)
            next_visuals = ['next', 'next page', '>', '→', 'chevron_right', 'right-arrow']
            for text in next_visuals:
                # Case insensitive search in text and aria-label
                xpath = f"//*[(self::a or self::button or self::span) and (contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}'))]"
                try:
                    elements = driver_instance.find_elements(By.XPATH, xpath)
                    for e in elements:
                        if e.is_displayed():
                            # Filter out 'Back to top' or unrelated arrows if possible
                            if 'top' in e.text.lower(): continue
                            indicators['next'] = True
                            signals.append(f"Heuristic: Found NEXT keyword/aria '{text}'")
                            structural_evidence_found = True
                            break
                    if indicators['next']: break
                except: continue

            # 1.2.1 Deep Workday/Platform Chevrons
            platform_next_selectors = [
                'button[aria-label*="Next"]', 'button[aria-label*="next"]',
                '.wd-paginator-next-button', '[data-automation-id="pageNext"]',
                '.next-page-button', '.sf-pagination-next'
            ]
            for sel in platform_next_selectors:
                try:
                    elements = driver_instance.find_elements(By.CSS_SELECTOR, sel)
                    if any(e.is_displayed() for e in elements):
                        indicators['next'] = True
                        signals.append(f"Heuristic: Found Next via platform selector '{sel}'")
                        structural_evidence_found = True
                        break
                except: continue

            # 1.3 Range patterns (Page 1 of 10)
            range_patterns = [
                r'results?\s+\d+\s*[-–—]\s*\d+\s+of\s+[\d,]+', 
                r'page\s+(\d+)\s+of\s+(\d+)', 
                r'\d+\s*-\s*\d+\s+of\s+\d+',
                r'items per page',
                r'jump to page'
            ]
            try:
                # New: Aggressive global search (Hatch/SuccessFactors fallback)
                p_text = driver_instance.execute_script("""
                    const sels = 'nav, footer, .pagination, .pager, .results, .result-count, .paginationShell, .nav-pagination, .srHelp, [class*="pagination"], [id*="pagination"], [class*="shell"]';
                    return Array.from(document.querySelectorAll(sels))
                        .map(el => (el.textContent || ""))
                        .join(" ").toLowerCase();
                """)
                
                # Check patterns
                found_range = False
                for pattern in range_patterns:
                    if re.search(pattern, p_text, re.IGNORECASE):
                        found_range = True
                        break
                
                # Fallback: check entire body textContent (slow but definitive)
                if not found_range:
                    p_text = driver_instance.execute_script("return document.body.textContent;").lower()
                    for pattern in range_patterns:
                        if re.search(pattern, p_text, re.IGNORECASE):
                            found_range = True
                            break

                if found_range:
                    indicators['pageselect'] = True
                    signals.append(f"Paginator Wall: Found range pattern (Page x of y)")
                    structural_evidence_found = True
                
                # 1.3.1 NEXT symbols (single arrows only - forward and backward)
                # Single arrows indicate sequential navigation (next/previous page)
                next_symbols = ['>', '→', '›', '‹', '←', '➜']
                for sym in next_symbols:
                    xpath = f"//*[(self::a or self::button or self::span) and (text()='{sym}' or contains(., '{sym}') or contains(@aria-label, '{sym}'))]"
                    try:
                        elements = driver_instance.find_elements(By.XPATH, xpath)
                        for e in elements:
                            if e.is_displayed():
                                # Filter out obvious non-pagination (back to top)
                                if 'top' in e.text.lower(): continue
                                indicators['next'] = True
                                signals.append(f"Heuristic: Found NEXT symbol '{sym}'")
                                structural_evidence_found = True
                                break
                        if indicators['next']: break
                    except: continue

                # 1.3.2 PAGESELECT keywords and symbols (checked AFTER next symbols)
                # Keywords: First, Last
                # Symbols: », >>, «, << (double arrows/jump buttons)
                if not indicators['pageselect']:
                    pageselect_keywords = ['first', 'last']
                    for keyword in pageselect_keywords:
                        xpath = f"//*[(self::a or self::button or self::span) and (contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}'))]"
                        try:
                            elements = driver_instance.find_elements(By.XPATH, xpath)
                            for e in elements:
                                if e.is_displayed():
                                    indicators['pageselect'] = True
                                    signals.append(f"Heuristic: Found PAGESELECT keyword '{keyword}'")
                                    structural_evidence_found = True
                                    break
                            if indicators['pageselect']: break
                        except: continue
                    
                    # PAGESELECT symbols (double arrows and jump buttons - backward and forward)
                    if not indicators['pageselect']:
                        pageselect_symbols = ['»', '>>', '«', '<<']
                        for sym in pageselect_symbols:
                            xpath = f"//*[(self::a or self::button or self::span) and (text()='{sym}' or contains(., '{sym}'))]"
                            try:
                                elements = driver_instance.find_elements(By.XPATH, xpath)
                                for e in elements:
                                    if e.is_displayed():
                                        indicators['pageselect'] = True
                                        signals.append(f"Heuristic: Found PAGESELECT symbol '{sym}'")
                                        structural_evidence_found = True
                                        break
                                if indicators['pageselect']: break
                            except: continue

                # 1.3.2 Numbered Button Groups (Hatch/Numbered Pagination)
                # Look for contiguous numbers 1, 2 in small area or siblings
                if not indicators['pageselect']:
                    # Try finding '1' and '2' as separate elements within any small container
                    pagination_containers = driver_instance.find_elements(By.CSS_SELECTOR, "nav, ul, div, [class*='pagination'], [class*='paging'], [class*='pager'], .active, .current, [id*='page'], [class*='page']")
                    for container in pagination_containers:
                        try:
                            if not container.is_displayed(): continue
                            ctext = container.text
                            # Use regex to find isolated numbers 1 and 2
                            # This catches patterns like "1 2 3" or "[ 1 ] [ 2 ]"
                            nums = re.findall(r'\b(1|2)\b', ctext)
                            if '1' in nums and '2' in nums and len(ctext) < 400:
                                indicators['pageselect'] = True
                                signals.append(f"Heuristic: Found '1' and '2' in container '{container.get_attribute('class') or container.get_attribute('id')}'")
                                structural_evidence_found = True
                                break
                        except: continue
                    
                # 1.3.2 Fallback: Check for any small element containing "1" and "2" nearby
                if not indicators['pageselect']:
                    try:
                        # Find all dots/numbers and check their neighborhood
                        elements = driver_instance.find_elements(By.XPATH, "//*[(self::a or self::button or self::span or self::div) and (text()='1' or .='1')]")
                        for e in elements:
                            if not e.is_displayed(): continue
                            # Ascend up to 2 levels to find a common container
                            curr = e
                            for _ in range(2):
                                try:
                                    curr = curr.find_element(By.XPATH, "./parent::*")
                                    p_text = curr.text
                                    nums = re.findall(r'\b(1|2)\b', p_text)
                                    if '1' in nums and '2' in nums and len(p_text) < 200:
                                        indicators['pageselect'] = True
                                        signals.append("Heuristic: Found '1' and '2' in local neighborhood")
                                        structural_evidence_found = True
                                        break
                                except: break
                            if indicators['pageselect']: break
                    except: pass
            except: pass

            return indicators, signals, has_autopager_results

        # --- HELPER: Extract page snippet for AI ---
        def get_page_snippet():
            try:
                return self.driver.execute_script("""
                    const paginationSelectors = 'nav, footer, .pagination, .pager, [class*="pagination"], [class*="paging"], [role="navigation"]';
                    let containers = Array.from(document.querySelectorAll(paginationSelectors));
                    if (containers.length === 0) containers = [document.body];
                    return containers.map(container => {
                        const interactive = Array.from(container.querySelectorAll('a, button, input, span'));
                        return interactive.slice(-30).map(el => {
                            const html = el.outerHTML.substring(0, 300);
                            const parent = el.parentElement ? el.parentElement.innerText.substring(0, 50) : "";
                            return `${html} (Parent: ${parent})`;
                        }).join('\\n');
                    }).join('\\n---CONTAINER---\\n').substring(0, 3500);
                """)
            except:
                return ""

        # ============================================================
        # STEP 1: EXTRACT SIGNALS (Main Frame + Iframes)
        # ============================================================
        final_indicators, detected_signals, has_autopager = extract_page_signals(self.driver)
        
        # Check iframes for structural signals if not convincingly found
        if not has_autopager:
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                for i, frame in enumerate(iframes):
                    try:
                        self.driver.switch_to.frame(frame)
                        f_indicators, f_signals, f_has_autopager = extract_page_signals(self.driver)
                        
                        if f_has_autopager:
                            has_autopager = True
                            # Merge signals and indicators
                            for key in final_indicators:
                                if f_indicators[key]: final_indicators[key] = True
                            detected_signals.extend([f"Iframe {i}: {s}" for s in f_signals])
                        
                        self.driver.switch_to.default_content()
                        if has_autopager: break
                    except:
                        self.driver.switch_to.default_content()
            except: pass
        
        if has_autopager:
            detected_signals.append("Branch: STRUCTURAL (Autopager detected paginator)")
            
            # --- Step 1: Check for NEXT rules ---
            if final_indicators['next']:
                decision = 'next'
                detected_signals.append("Structural: NEXT rules matched (arrow, chevron, or 'next' text)")
                print(f"  Signals: {'; '.join(detected_signals)}")
                return decision, f"Final decision: {decision}. Signals: {detected_signals}"
            
            # --- Step 2: Check for PAGESELECT rules ---
            if final_indicators['pageselect']:
                decision = 'pageselect'
                detected_signals.append("Structural: PAGESELECT rules matched (numbers/labels)")
                print(f"  Signals: {'; '.join(detected_signals)}")
                return decision, f"Final decision: {decision}. Signals: {detected_signals}"
            
            # --- Step 3: Ambiguous - Try AI Judge if available ---
            detected_signals.append("Structural: Ambiguous signals, consulting AI Judge...")
            if self.api_key:
                page_snippet = get_page_snippet(self.driver)
                ai_decision = self.ai_judge.ask(url, detected_signals, page_snippet, branch='structural')
                if ai_decision and ai_decision in ['next', 'pageselect']:
                    detected_signals.append(f"AI Judge decided: {ai_decision}")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return ai_decision, f"Final decision: {ai_decision}. Signals: {detected_signals}"
            
            # --- Step 4: Fallback to NEXT (STRUCTURAL PATH default) ---
            decision = 'next'
            detected_signals.append("Structural Fallback: Paginator detected but ambiguous → defaulting to 'next'")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return decision, f"Final decision: {decision}. Signals: {detected_signals}"
        
        # ============================================================
        # BRANCH 2: BEHAVIORAL (Autopager detected NO paginator)
        # Order: Scrolldown → LoadMore → AI Judge → Fallback to 'next'
        # ============================================================
        detected_signals.append("Branch: BEHAVIORAL (Autopager NO paginator)")
        
        # --- Step 1: Scrolldown Verification (FIRST) ---
        # Per USER: Wait 2nd-3s to let website load initially
        time.sleep(3)
        
        detected_signals.append("Behavioral: Testing Scrolldown FIRST...")
        scrolldown_confirmed = False
        
        # Initial state measurements
        initial_url = self.driver.current_url.split('#')[0].rstrip('/')
        initial_height = self.get_page_height()
        initial_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        # First Scroll Attempt
        self.scroll_to_bottom()
        time.sleep(2) # Per USER: wait for 2 sec
        
        current_url = self.driver.current_url.split('#')[0].rstrip('/')
        final_height = self.get_page_height()
        final_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        # Hard crawler rules for scrolldown: height increase, elements increase, URL unchanged
        if (final_height > initial_height + 400 or final_element_count > initial_element_count + 8) and current_url == initial_url:
            detected_signals.append("Behavioral: Scrolldown CONFIRMED (attempt 1 - 2s wait)")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
        
        # Second Scroll Attempt (Longer wait)
        detected_signals.append("Scroll attempt 1: No growth. Waiting 5s for retry...")
        time.sleep(5) # Per USER: wait for 3-5 sec more
        self.scroll_to_bottom()
        time.sleep(2)
        
        current_url = self.driver.current_url.split('#')[0].rstrip('/')
        final_height = self.get_page_height()
        final_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        if (final_height > initial_height + 400 or final_element_count > initial_element_count + 8) and current_url == initial_url:
            detected_signals.append("Behavioral: Scrolldown CONFIRMED (attempt 2 - 5s additional wait)")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
        else:
            detected_signals.append("Scroll attempt 2: Still no scrolldown growth.")
        
        # --- Step 2: LoadMore Button Detection ---
        detected_signals.append("Behavioral: Scrolldown failed, testing LoadMore button...")
        loadmore_button = None
        
        # Common LoadMore button patterns
        loadmore_keywords = [
            "load more", "show more", "view more", "see more", "load all",
            "show all", "view all", "see all", "more jobs", "more results",
            "load additional", "show additional"
        ]
        
        try:
            # Search for buttons with LoadMore keywords
            for keyword in loadmore_keywords:
                # Try button tags
                buttons = self.driver.find_elements(By.XPATH, 
                    f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]")
                if buttons and buttons[0].is_displayed():
                    loadmore_button = buttons[0]
                    detected_signals.append(f"LoadMore button found: '{keyword}'")
                    break
                
                # Try links/anchors
                if not loadmore_button:
                    links = self.driver.find_elements(By.XPATH, 
                        f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]")
                    if links and links[0].is_displayed():
                        loadmore_button = links[0]
                        detected_signals.append(f"LoadMore link found: '{keyword}'")
                        break
            
            # If LoadMore button found, verify it's clickable
            if loadmore_button:
                try:
                    # Record state before click
                    before_height = self.get_page_height()
                    before_elements = len(self.driver.find_elements(By.XPATH, "//*"))
                    
                    # Click the button
                    loadmore_button.click()
                    time.sleep(3)  # Wait for content to load
                    
                    # Check if content increased
                    after_height = self.get_page_height()
                    after_elements = len(self.driver.find_elements(By.XPATH, "//*"))
                    
                    if after_height > before_height + 200 or after_elements > before_elements + 5:
                        detected_signals.append("Behavioral: LoadMore CONFIRMED (content increased after click)")
                        print(f"  Signals: {'; '.join(detected_signals)}")
                        return 'loadmore', f"Final decision: loadmore. Signals: {detected_signals}"
                    else:
                        detected_signals.append("LoadMore button clicked but no content increase detected")
                except Exception as e:
                    detected_signals.append(f"LoadMore button found but click failed: {str(e)[:50]}")
            else:
                detected_signals.append("Behavioral: No LoadMore button found")
        except Exception as e:
            detected_signals.append(f"LoadMore detection error: {str(e)[:50]}")
        
        # --- Step 3: AI Judge (if ambiguous) ---
        detected_signals.append("Behavioral: Ambiguous, consulting AI Judge...")
        if self.api_key:
            page_snippet = get_page_snippet(self.driver)
            ai_decision = self.ai_judge.ask(url, detected_signals, page_snippet, branch='behavioral')
            if ai_decision and ai_decision in ['loadmore', 'scrolldown']:
                detected_signals.append(f"AI Judge decided: {ai_decision}")
                print(f"  Signals: {'; '.join(detected_signals)}")
                return ai_decision, f"Final decision: {ai_decision}. Signals: {detected_signals}"
        
        # --- Step 4: Fallback to NEXT (BEHAVIORAL PATH default) ---
        decision = 'next'
        detected_signals.append("Behavioral Fallback: No scrolldown, no loadmore button → defaulting to 'next'")
        print(f"  Signals: {'; '.join(detected_signals)}")
        return decision, f"Final decision: {decision}. Signals: {detected_signals}"
    
    def use_ai_judge_fallback(self, url):
        """
        Public method to call AI Judge when page loading fails.
        Returns (bucket, reason) tuple.
        """
        if not self.api_key:
            return None, "No API key for AI Judge"
        
        return self.ai_judge.fallback_classify(url)

# Lock for synchronized terminal printing and CSV writing
print_lock = threading.Lock()
csv_lock = threading.Lock()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=str, default='test.csv', help='Input CSV file')
    parser.add_argument('--output', type=str, default='output.csv', help='Output CSV file')
    parser.add_argument('--workers', type=int, default=5, help='Number of parallel workers')
    parser.add_argument('--api-key', type=str, default=os.getenv("ANTHROPIC_API_KEY"), help='Claude API Key (optional)')
    parser.add_argument('--reprocess', action='store_true', help='Force reprocess all rows even if already classified')
    args = parser.parse_args()
    
    # Pass API key globally to workers
    def process_csv_with_key(input_file, output_file, num_workers, api_key, force_reprocess=False):
        total_start = time.time()
        try:
            df = pd.read_csv(input_file)
            
            # 1. Resuming logic: If output file exists, load it
            if os.path.exists(output_file) and not force_reprocess:
                print(f"Resuming from existing {output_file}...")
                try:
                    # Check if file has content before trying to parse
                    if os.path.getsize(output_file) > 10:  # More than just newlines
                        existing_df = pd.read_csv(output_file)
                        # Map existing results back to main df using URL as key (or index if structure matches)
                        if 'flow' in existing_df.columns:
                            # We assume the order is preserved or use URL as anchor
                            # Simplest: if lengths match, just merge the 'flow' column
                            if len(existing_df) == len(df):
                                df['flow'] = existing_df['flow']
                            else:
                                df['flow'] = ""
                        else:
                            df['flow'] = ""
                    else:
                        print(f"  Output file is empty, starting fresh...")
                        df['flow'] = ""
                except Exception as e:
                    print(f"  Could not read existing output ({e}), starting fresh...")
                    df['flow'] = ""
            else:
                df['flow'] = ""

            col_name = None
            possible_cols = ['companyUrl', 'url', 'link', 'Website', 'career_page_url']
            for col in possible_cols:
                if col in df.columns:
                    col_name = col
                    break
            if not col_name:
                for col in df.columns:
                    if df[col].astype(str).str.contains('http').any():
                        col_name = col
                        break
            if not col_name: col_name = df.columns[0]

            # 2. Filter tasks: Process only empty or "error" rows
            active_tasks = []
            reprocess_count = 0
            for index, row in df.iterrows():
                url = row[col_name]
                if pd.isna(url) or str(url).strip() == '' or str(url).lower() == 'nan':
                     continue
                
                current_flow = str(row.get('flow', '')).lower().strip()
                # Process if: empty, nan, or starts with "error"
                is_error = current_flow.startswith('error') or current_flow == 'nan' or current_flow == ''
                
                if force_reprocess or is_error:
                    url = str(url).strip()
                    if not url.startswith(('http://', 'https://')): url = 'https://' + url
                    active_tasks.append((index, url))
                    if is_error and not force_reprocess: reprocess_count += 1

            if not active_tasks:
                print("All rows already classified. Use --reprocess to force a full run.")
                return

            print(f"Processing {len(active_tasks)} URLs ({reprocess_count} retries/new) using {num_workers} workers...")
            if api_key:
                print(f"AI Judge ENABLED (Cost-optimized: only for unsure cases)")
            else:
                print(f"AI Judge DISABLED (Using heuristics only)")
            
            # Initial write to preserve already finished rows
            df[df['flow'].astype(str).str.len() > 0].to_csv(output_file, index=False)

            # Updated worker_task definition to handle api_key
            def worker_task_with_key(index, url, output_file, df_shared, key):
                classifier = None
                try:
                    classifier = PaginationClassifier(api_key=key)
                    start_time = time.time()
                    with print_lock:
                        print(f"[{index}] Starting: {url}")
                    bucket, signals_reason = classifier.classify_url(url)
                    elapsed = time.time() - start_time
                    with csv_lock:
                        df_shared.at[index, 'flow'] = bucket
                        df_live = df_shared[df_shared['flow'].astype(str).str.len() > 0].copy()
                        # Force immediate write with flush
                        with open(output_file, 'w') as f:
                            df_live.sort_index().to_csv(f, index=False)
                            f.flush()
                            os.fsync(f.fileno())
                    with print_lock:
                        print(f"[{index}] Result: {bucket.upper()} ({elapsed:.2f}s)")
                except Exception as e:
                    with print_lock:
                        print(f"[{index}] Error on {url}: {e}")
                    with csv_lock:
                        df_shared.at[index, 'flow'] = f"error: {str(e)[:50]}"
                        df_shared[df_shared['flow'].astype(str).str.len() > 0].sort_index().to_csv(output_file, index=False)
                finally:
                    if classifier: classifier.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(worker_task_with_key, idx, url, output_file, df, api_key) for idx, url in active_tasks]
                concurrent.futures.wait(futures)

            df.sort_index().to_csv(output_file, index=False)
            total_elapsed = time.time() - total_start
            print(f"\nProcessing Complete!")
            print(f"Successfully saved results to {output_file}")
        except Exception as e:
            print(f"Fatal error: {e}")

    process_csv_with_key(args.input, args.output, args.workers, args.api_key, args.reprocess)
