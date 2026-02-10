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
        
        # Additional options to prevent zombie processes and reduce CPU usage
        chrome_options.add_argument("--disable-gpu")  # Disable GPU acceleration
        chrome_options.add_argument("--disable-extensions")  # Disable extensions
        chrome_options.add_argument("--disable-software-rasterizer")  # Reduce CPU usage
        chrome_options.add_argument("--disable-background-networking")  # Prevent background requests
        chrome_options.add_argument("--disable-default-apps")  # Disable default apps
        chrome_options.add_argument("--disable-sync")  # Disable Chrome sync
        chrome_options.add_argument("--disable-translate")  # Disable translate
        chrome_options.add_argument("--metrics-recording-only")  # Reduce overhead
        chrome_options.add_argument("--mute-audio")  # Mute audio
        chrome_options.add_argument("--no-first-run")  # Skip first run
        chrome_options.add_argument("--safebrowsing-disable-auto-update")  # Disable safebrowsing updates
        chrome_options.add_argument("--disable-features=TranslateUI")  # Disable translate UI
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")  # Hide automation

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
        
        # Create service with explicit cleanup
        self.service = Service(driver_path)
        self.driver = webdriver.Chrome(service=self.service, options=chrome_options)
        self.driver.set_page_load_timeout(60)  # Initial timeout: 60 seconds
        self.ai_judge = AIJudge(api_key=api_key)

    def close(self):
        """Properly cleanup browser and driver to prevent resource leaks"""
        try:
            if self.driver:
                # Close all windows first
                try:
                    for handle in self.driver.window_handles:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                except:
                    pass
                
                # Then quit the driver
                self.driver.quit()
        except Exception as e:
            print(f"Warning: Error closing driver: {e}")
        finally:
            self.driver = None
            
        # Also stop the service if it exists
        try:
            if hasattr(self, 'service') and self.service:
                self.service.stop()
        except Exception as e:
            print(f"Warning: Error stopping service: {e}")
        finally:
            self.service = None
    
    def __del__(self):
        """Destructor to ensure cleanup even if close() is not called"""
        try:
            self.close()
        except:
            pass

    def get_page_height(self):
        return self.driver.execute_script("return document.body.scrollHeight")

    def scroll_to_bottom(self):
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2) # Wait for potential load

    def classify_url(self, url, print_callback=None):
        try:
            # Robust loading with retry - progressive wait times
            max_retries = 3
            wait_times = [5, 7, 10]  # Progressive wait: 5s -> 7s -> 10s
            timeout_limits = [30, 45, 60]  # Progressive timeout: 30s -> 45s -> 60s
            
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
                    time.sleep(5)  # Wait 5 seconds before retry
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
        
        def is_truly_visible(element):
            """Enhanced visibility check - ensures element is actually visible to users"""
            try:
                # Basic visibility check
                if not element.is_displayed():
                    return False
                
                # Check if element has size (not 0x0)
                size = element.size
                if size['width'] <= 0 or size['height'] <= 0:
                    return False
                
                # Check CSS visibility and opacity
                visibility = element.value_of_css_property('visibility')
                opacity = element.value_of_css_property('opacity')
                display = element.value_of_css_property('display')
                
                if visibility == 'hidden' or display == 'none':
                    return False
                
                if opacity:
                    try:
                        if float(opacity) < 0.1:  # Nearly invisible
                            return False
                    except:
                        pass
                
                # Check if element is in viewport or at least positioned on page
                location = element.location
                if location['x'] < -9999 or location['y'] < -9999:  # Hidden off-screen
                    return False
                
                return True
            except:
                return False
        
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
                    
                    # Enhanced Visibility Check: Verify element is truly visible to users
                    is_visible = False
                    try:
                        # Construct a unique-ish search
                        search_xpath = f"//a[@href='{href}']" if href else f"//*[contains(text(), '{text[:20]}')]"
                        elements = driver_instance.find_elements(By.XPATH, search_xpath)
                        if any(is_truly_visible(e) for e in elements):
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

            # 1.2 Keyword Search (Next/Arrow) - ONLY BUTTONS AND ANCHORS
            next_visuals = ['next', 'next page', '>', '→', 'chevron_right', 'right-arrow']
            for text in next_visuals:
                # Only search for clickable elements (buttons and anchor tags with href)
                xpath = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')] | //a[@href and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]"
                try:
                    elements = driver_instance.find_elements(By.XPATH, xpath)
                    for e in elements:
                        if is_truly_visible(e):
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
                    if any(is_truly_visible(e) for e in elements):
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
                
                # 1.3.1 NEXT keywords and symbols
                # Keywords: next, previous, prev
                # Symbols: Single arrows (>, →, ›, ‹, ←, ➜)
                # ONLY search for buttons and anchor tags, NOT plain text
                
                # Check NEXT keywords first (next, previous, prev)
                next_keywords = ['next', 'Next', 'NEXT']
                for keyword in next_keywords:
                    xpath = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')] | //a[@href and (contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}'))]"
                    try:
                        elements = driver_instance.find_elements(By.XPATH, xpath)
                        for e in elements:
                            if is_truly_visible(e):
                                # Filter out obvious non-pagination (back to top)
                                if 'top' in e.text.lower(): continue
                                indicators['next'] = True
                                signals.append(f"Heuristic: Found NEXT keyword '{keyword}'")
                                structural_evidence_found = True
                                break
                        if indicators['next']: break
                    except: continue
                
                # Check NEXT symbols if keywords not found
                if not indicators['next']:
                    next_symbols = ['>', '→', '›', '‹', '←', '➜']
                    for sym in next_symbols:
                        xpath = f"//button[text()='{sym}' or contains(., '{sym}') or contains(@aria-label, '{sym}')] | //a[@href and (text()='{sym}' or contains(., '{sym}') or contains(@aria-label, '{sym}'))]"
                        try:
                            elements = driver_instance.find_elements(By.XPATH, xpath)
                            for e in elements:
                                if is_truly_visible(e):
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
                # ONLY search for buttons and anchor tags, NOT plain text
                if not indicators['pageselect']:
                    pageselect_keywords = ['first', 'last','FIRST','LAST']
                    for keyword in pageselect_keywords:
                        xpath = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')] | //a[@href and (contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}') or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}'))]"
                        try:
                            elements = driver_instance.find_elements(By.XPATH, xpath)
                            for e in elements:
                                if is_truly_visible(e):
                                    indicators['pageselect'] = True
                                    signals.append(f"Heuristic: Found PAGESELECT keyword '{keyword}'")
                                    structural_evidence_found = True
                                    break
                            if indicators['pageselect']: break
                        except: continue
                    
                    # PAGESELECT symbols (double arrows and jump buttons - backward and forward)
                    # ONLY search for buttons and anchor tags, NOT plain text
                    if not indicators['pageselect']:
                        pageselect_symbols = ['»', '>>', '«', '<<']
                        for sym in pageselect_symbols:
                            xpath = f"//button[text()='{sym}' or contains(., '{sym}')] | //a[@href and (text()='{sym}' or contains(., '{sym}'))]"
                            try:
                                elements = driver_instance.find_elements(By.XPATH, xpath)
                                for e in elements:
                                    if is_truly_visible(e):
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
                            if not is_truly_visible(container): continue
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
                            if not is_truly_visible(e): continue
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
        # FALLBACK: Manual Button/Anchor Detection (when Autopager says NO)
        # Check for pagination buttons and anchor tags that Autopager might have missed
        # ============================================================
        detected_signals.append("Autopager found NO paginator - checking manual button/anchor detection...")
        
        # Check for buttons with anchor tags inside or standalone anchors
        manual_next = False
        manual_pageselect = False
        manual_loadmore = False
        
        try:
            # --- Check 1: Buttons containing anchor tags with NEXT patterns ---
            next_patterns = ['next', 'previous', 'prev', '>', '<', '→', '←', '›', '‹']
            for pattern in next_patterns:
                # Check buttons with text
                xpath_button = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}')]"
                buttons = self.driver.find_elements(By.XPATH, xpath_button)
                for btn in buttons:
                    if is_truly_visible(btn):
                        # Check if button contains an anchor or is clickable
                        anchors = btn.find_elements(By.TAG_NAME, 'a')
                        if anchors or btn.is_enabled():
                            manual_next = True
                            detected_signals.append(f"Manual Detection: Found button with NEXT pattern '{pattern}'")
                            break
                if manual_next:
                    break
                
                # Check anchor tags with NEXT patterns
                if not manual_next:
                    xpath_anchor = f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}') and @href]"
                    anchors = self.driver.find_elements(By.XPATH, xpath_anchor)
                    for anchor in anchors:
                        if is_truly_visible(anchor) and anchor.get_attribute('href'):
                            href = anchor.get_attribute('href')
                            # Skip javascript:void, # anchors, and external domains
                            if href and not href.startswith('javascript:') and not href == '#':
                                manual_next = True
                                detected_signals.append(f"Manual Detection: Found anchor with NEXT pattern '{pattern}'")
                                break
                if manual_next:
                    break
            
            # --- Check 2: Buttons/Anchors with PAGESELECT patterns ---
            if not manual_pageselect:
                pageselect_patterns = ['first', 'last', '»', '>>', '«', '<<']
                for pattern in pageselect_patterns:
                    # Check buttons
                    xpath_button = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}')]"
                    buttons = self.driver.find_elements(By.XPATH, xpath_button)
                    for btn in buttons:
                        if is_truly_visible(btn):
                            manual_pageselect = True
                            detected_signals.append(f"Manual Detection: Found button with PAGESELECT pattern '{pattern}'")
                            break
                    if manual_pageselect:
                        break
                    
                    # Check anchors
                    if not manual_pageselect:
                        xpath_anchor = f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}') and @href]"
                        anchors = self.driver.find_elements(By.XPATH, xpath_anchor)
                        for anchor in anchors:
                            if is_truly_visible(anchor) and anchor.get_attribute('href'):
                                href = anchor.get_attribute('href')
                                if href and not href.startswith('javascript:') and not href == '#':
                                    manual_pageselect = True
                                    detected_signals.append(f"Manual Detection: Found anchor with PAGESELECT pattern '{pattern}'")
                                    break
                    if manual_pageselect:
                        break
                
                # Check for numbered page links (1, 2, 3)
                if not manual_pageselect:
                    for num in ['1', '2', '3']:
                        xpath = f"//a[@href and text()='{num}']"
                        elements = self.driver.find_elements(By.XPATH, xpath)
                        if len(elements) >= 2:  # At least 2 numbered links
                            manual_pageselect = True
                            detected_signals.append("Manual Detection: Found numbered page links (1, 2, 3)")
                            break
            
            # --- Check 3: Buttons/Anchors with LOADMORE patterns ---
            if not manual_loadmore:
                loadmore_patterns = [
                    'load more', 'show more', 'view more', 'see more', 'load all', 'show all',
                    'view more jobs', 'view all jobs', 'see all jobs', 'more jobs', 'all jobs',
                    'view jobs', 'show jobs', 'load jobs', 'see jobs'
                ]
                for pattern in loadmore_patterns:
                    # Check buttons
                    xpath_button = f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}')]"
                    buttons = self.driver.find_elements(By.XPATH, xpath_button)
                    for btn in buttons:
                        if is_truly_visible(btn):
                            manual_loadmore = True
                            detected_signals.append(f"Manual Detection: Found button with LOADMORE pattern '{pattern}'")
                            break
                    if manual_loadmore:
                        break
                    
                    # Check anchors with LoadMore keywords
                    if not manual_loadmore:
                        xpath_anchor = f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}') and @href]"
                        anchors = self.driver.find_elements(By.XPATH, xpath_anchor)
                        for anchor in anchors:
                            if is_truly_visible(anchor):
                                manual_loadmore = True
                                detected_signals.append(f"Manual Detection: Found anchor with LOADMORE pattern '{pattern}'")
                                break
                    if manual_loadmore:
                        break
        
        except Exception as e:
            detected_signals.append(f"Manual detection error: {str(e)[:50]}")
        
        # --- Return based on manual detection (NEXT has priority) ---
        if manual_next:
            detected_signals.append("Manual Detection: NEXT patterns found")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return 'next', f"Final decision: next (manual detection). Signals: {detected_signals}"
        
        if manual_pageselect:
            detected_signals.append("Manual Detection: PAGESELECT patterns found")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return 'pageselect', f"Final decision: pageselect (manual detection). Signals: {detected_signals}"
        
        if manual_loadmore:
            detected_signals.append("Manual Detection: LOADMORE patterns found")
            print(f"  Signals: {'; '.join(detected_signals)}")
            return 'loadmore', f"Final decision: loadmore (manual detection). Signals: {detected_signals}"
        
        # ============================================================
        # BRANCH 2: BEHAVIORAL (Autopager NO paginator, Manual detection also failed)
        # Order: LoadMore → Scrolldown → AI Judge → Fallback to 'next'
        # ============================================================
        detected_signals.append("Branch: BEHAVIORAL (No pagination detected by Autopager or manual detection)")
        
        # --- Step 1: LoadMore Button Detection (FIRST) ---
        detected_signals.append("Behavioral: Testing LoadMore button FIRST...")
        loadmore_button = None
        
        # Common LoadMore button patterns
        loadmore_keywords = [
            "load more", "show more", "view more", "see more", "load all",
            "show all", "view all", "see all", "more jobs", "more results",
            "load additional", "show additional", "view more jobs", "view all jobs",
            "see all jobs", "all jobs", "view jobs", "show jobs", "load jobs", "see jobs"
        ]
        
        try:
            # Search for buttons with LoadMore keywords
            for keyword in loadmore_keywords:
                # Try button tags
                buttons = self.driver.find_elements(By.XPATH, 
                    f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]")
                if buttons and is_truly_visible(buttons[0]):
                    loadmore_button = buttons[0]
                    detected_signals.append(f"LoadMore button found: '{keyword}'")
                    break
                
                # Try links/anchors with LoadMore keywords
                if not loadmore_button:
                    links = self.driver.find_elements(By.XPATH, 
                        f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]")
                    if links and is_truly_visible(links[0]):
                        loadmore_button = links[0]
                        detected_signals.append(f"LoadMore link found: '{keyword}'")
                        break
                
                if loadmore_button:
                    break
        except Exception as e:
            detected_signals.append(f"LoadMore detection error: {str(e)[:50]}")
        
        # If LoadMore button found, test it by clicking
        if loadmore_button:
            try:
                detected_signals.append("Behavioral: LoadMore button detected, testing click...")
                initial_element_count_lm = len(self.driver.find_elements(By.XPATH, "//*"))
                
                # Click the LoadMore button
                self.driver.execute_script("arguments[0].scrollIntoView(true);", loadmore_button)
                time.sleep(1)
                loadmore_button.click()
                time.sleep(3)  # Wait for content to load
                
                final_element_count_lm = len(self.driver.find_elements(By.XPATH, "//*"))
                
                # Check if content increased after clicking
                if final_element_count_lm > initial_element_count_lm + 5:
                    detected_signals.append(f"Behavioral: LoadMore CONFIRMED (elements: {initial_element_count_lm} → {final_element_count_lm})")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return 'loadmore', f"Final decision: loadmore. Signals: {detected_signals}"
                else:
                    detected_signals.append("LoadMore button found but no content increase after click")
            except Exception as e:
                detected_signals.append(f"LoadMore click test failed: {str(e)[:50]}")
        
        # --- Step 2: Scrolldown Verification (if LoadMore failed) ---
        # Per USER: Wait 2nd-3s to let website load initially
        time.sleep(3)
        
        detected_signals.append("Behavioral: Testing Scrolldown with header/footer awareness...")
        scrolldown_confirmed = False
        
        # Initial state measurements
        initial_url = self.driver.current_url.split('#')[0].rstrip('/')
        initial_height = self.get_page_height()
        initial_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        # Detect footer presence
        has_footer = False
        footer_position = None
        try:
            # Look for footer elements
            footer_selectors = [
                "//footer", "//div[@id='footer']", "//div[@class='footer']",
                "//div[contains(@class, 'footer')]", "//div[contains(@id, 'footer')]"
            ]
            for selector in footer_selectors:
                footers = self.driver.find_elements(By.XPATH, selector)
                if footers and is_truly_visible(footers[0]):
                    has_footer = True
                    footer_position = footers[0].location['y']
                    detected_signals.append(f"Detected footer at position Y={footer_position}")
                    break
        except:
            pass
        
        # Measure content area (between header and footer if they exist)
        try:
            # Try to find main content area
            content_area = None
            content_selectors = [
                "//main", "//div[@role='main']", "//div[@id='content']",
                "//div[contains(@class, 'content')]", "//div[contains(@class, 'jobs')]",
                "//div[contains(@class, 'listings')]", "//body"
            ]
            for selector in content_selectors:
                elements = self.driver.find_elements(By.XPATH, selector)
                if elements and is_truly_visible(elements[0]):
                    content_area = elements[0]
                    break
            
            if content_area:
                initial_content_elements = len(content_area.find_elements(By.XPATH, ".//*"))
                detected_signals.append(f"Content area has {initial_content_elements} initial elements")
        except:
            initial_content_elements = initial_element_count
        
        # First Scroll Attempt
        self.scroll_to_bottom()
        time.sleep(2) # Per USER: wait for 2 sec
        
        current_url = self.driver.current_url.split('#')[0].rstrip('/')
        final_height = self.get_page_height()
        final_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        # Measure content growth
        content_grew = False
        try:
            if content_area:
                final_content_elements = len(content_area.find_elements(By.XPATH, ".//*"))
                if final_content_elements > initial_content_elements + 5:
                    content_grew = True
                    detected_signals.append(f"Content area grew from {initial_content_elements} to {final_content_elements} elements")
        except:
            pass
        
        # Scrolldown rules:
        # 1. If has footer: Content between header/footer should grow
        # 2. If no footer: Page height should keep increasing
        # 3. URL should remain the same (no navigation)
        if current_url == initial_url:
            if has_footer:
                # With footer: Check if content area expanded (not just footer moved down)
                if content_grew or (final_element_count > initial_element_count + 8):
                    detected_signals.append("Behavioral: Scrolldown CONFIRMED (content between header/footer expanded)")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
            else:
                # No footer: Check if page keeps growing (infinite scroll)
                if final_height > initial_height + 400 or final_element_count > initial_element_count + 8:
                    detected_signals.append("Behavioral: Scrolldown CONFIRMED (no footer, page keeps growing)")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
        
        # Second Scroll Attempt (Longer wait for slow-loading content)
        detected_signals.append("Scroll attempt 1: No growth. Waiting 5s for retry...")
        time.sleep(5) # Per USER: wait for 3-5 sec more
        self.scroll_to_bottom()
        time.sleep(2)
        
        current_url = self.driver.current_url.split('#')[0].rstrip('/')
        final_height = self.get_page_height()
        final_element_count = len(self.driver.find_elements(By.XPATH, "//*"))
        
        # Re-check content growth
        content_grew = False
        try:
            if content_area:
                final_content_elements = len(content_area.find_elements(By.XPATH, ".//*"))
                if final_content_elements > initial_content_elements + 5:
                    content_grew = True
                    detected_signals.append(f"Content area grew from {initial_content_elements} to {final_content_elements} elements (attempt 2)")
        except:
            pass
        
        if current_url == initial_url:
            if has_footer:
                if content_grew or (final_element_count > initial_element_count + 8):
                    detected_signals.append("Behavioral: Scrolldown CONFIRMED (content expanded, attempt 2)")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
            else:
                if final_height > initial_height + 400 or final_element_count > initial_element_count + 8:
                    detected_signals.append("Behavioral: Scrolldown CONFIRMED (page keeps growing, attempt 2)")
                    print(f"  Signals: {'; '.join(detected_signals)}")
                    return 'scrolldown', f"Final decision: scrolldown. Signals: {detected_signals}"
        
        detected_signals.append("Scroll attempt 2: Still no scrolldown growth.")
        
        # --- Step 3: AI Judge (if LoadMore and Scrolldown both failed) ---
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
