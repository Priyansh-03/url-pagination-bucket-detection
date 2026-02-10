import pandas as pd
import os
import time
import argparse
import threading
from queue import Queue
from classifier import PaginationClassifier

# Locks for thread-safe operations
print_lock = threading.Lock()
file_lock = threading.Lock()

def find_url_column(df):
    """Find the URL column in the dataframe"""
    possible_cols = ['companyUrl', 'url', 'link', 'Website', 'career_page_url', 'Website URL']
    
    # Try exact match
    for col in possible_cols:
        if col in df.columns:
            return col
    
    # Try case-insensitive match
    for col in df.columns:
        if col.lower() in [c.lower() for c in possible_cols]:
            return col
    
    # Find column containing URLs
    for col in df.columns:
        if df[col].astype(str).str.contains('http').any():
            return col
    
    # Default to first column
    return df.columns[0]

def save_results_live(output_file, df_results, col_name):
    """Save results maintaining exact row order from input"""
    with file_lock:
        # Create output dataframe preserving all columns from input
        df_out = df_results.copy()
        
        # Rename the URL column to 'companyUrl' for output if needed
        if col_name != 'companyUrl' and col_name in df_out.columns:
            df_out = df_out.rename(columns={col_name: 'companyUrl'})
        
        # Select only companyUrl and bucket columns for output
        if 'companyUrl' in df_out.columns and 'bucket' in df_out.columns:
            df_out = df_out[['companyUrl', 'bucket']]
        
        # Save with space after comma for better readability
        df_out.to_csv(output_file, index=False, sep=',', lineterminator='\n')
        
        # Read and reformat with space after comma
        with open(output_file, 'r') as f:
            content = f.read()
        
        # Add space after commas (but not in URLs that might have commas)
        lines = content.split('\n')
        formatted_lines = []
        for line in lines:
            if line.strip():
                # Split only on first comma to preserve any commas in bucket values
                parts = line.split(',', 1)
                if len(parts) == 2:
                    formatted_lines.append(f"{parts[0]}, {parts[1]}")
                else:
                    formatted_lines.append(line)
            else:
                formatted_lines.append(line)
        
        with open(output_file, 'w') as f:
            f.write('\n'.join(formatted_lines))

def safe_print(message):
    """Thread-safe print function"""
    with print_lock:
        print(message)

def worker(worker_id, task_queue, df_results, output_file, col_name, total_count, processed_urls, headless, api_key):
    """Worker thread to process URLs from the queue"""
    classifier = PaginationClassifier(api_key=api_key, headless=headless)
    
    try:
        while True:
            task = None
            try:
                task = task_queue.get(timeout=1)
                if task is None:  # Poison pill
                    break
                
                row_index, url = task
                
                # Check if this URL was already processed (safety check)
                with print_lock:
                    if url in processed_urls:
                        safe_print(f"[Worker {worker_id}] ⚠️  Skipping duplicate: {url}")
                        task_queue.task_done()
                        continue
                    processed_urls.add(url)
                    completed = df_results['bucket'].notna().sum()
                    print(f"[Worker {worker_id}] [{completed+1}/{total_count}] 🔄 Processing Row {row_index+1}: {url}")
                
                start_time = time.time()
                max_retries = 3
                bucket = None
                last_error = None
                
                # Try classification with retries
                for attempt in range(max_retries):
                    try:
                        bucket, reason = classifier.classify_url(url, print_callback=safe_print)
                        
                        # Check if result is an error
                        if bucket and bucket.startswith('error:'):
                            last_error = bucket
                            if attempt < max_retries - 1:
                                with print_lock:
                                    print(f"[Worker {worker_id}] ⚠️  Attempt {attempt+1}/{max_retries} failed: {bucket}")
                                    print(f"[Worker {worker_id}] 🔄 Retrying in 3 seconds...")
                                time.sleep(3)
                                continue
                            else:
                                # Last attempt failed - try AI Judge if available
                                if api_key:
                                    with print_lock:
                                        print(f"[Worker {worker_id}] 🤖 All retries failed. Trying AI Judge...")
                                    try:
                                        # Call AI Judge fallback
                                        ai_bucket, ai_reason = classifier.use_ai_judge_fallback(url)
                                        if ai_bucket:
                                            bucket = ai_bucket
                                            reason = ai_reason
                                            with print_lock:
                                                print(f"[Worker {worker_id}] ✨ AI Judge: {bucket}")
                                        else:
                                            bucket = "error: timeout_max_retries"
                                    except Exception as e:
                                        with print_lock:
                                            print(f"[Worker {worker_id}] ⚠️  AI Judge failed: {e}")
                                        bucket = "error: timeout_max_retries"
                                else:
                                    bucket = "error: timeout_max_retries"
                                break
                        else:
                            # Success!
                            break
                            
                    except Exception as e:
                        last_error = str(e)
                        if attempt < max_retries - 1:
                            with print_lock:
                                print(f"[Worker {worker_id}] ⚠️  Attempt {attempt+1}/{max_retries} exception: {e}")
                                print(f"[Worker {worker_id}] 🔄 Retrying in 3 seconds...")
                            time.sleep(3)
                        else:
                            # Last attempt - try AI Judge if API key available
                            if api_key:
                                with print_lock:
                                    print(f"[Worker {worker_id}] 🤖 All retries failed. Using AI Judge as fallback...")
                                try:
                                    # Call AI Judge fallback
                                    ai_bucket, ai_reason = classifier.use_ai_judge_fallback(url)
                                    if ai_bucket:
                                        bucket = ai_bucket
                                        reason = ai_reason
                                        with print_lock:
                                            print(f"[Worker {worker_id}] ✨ AI Judge: {bucket}")
                                    else:
                                        bucket = f"error: {str(e)[:50]}"
                                except Exception as ai_error:
                                    with print_lock:
                                        print(f"[Worker {worker_id}] ⚠️  AI Judge failed: {ai_error}")
                                    bucket = f"error: {str(e)[:50]}"
                            else:
                                bucket = f"error: {str(e)[:50]}"
                
                elapsed = time.time() - start_time
                
                # If still error after retries and no API key, keep the error
                if not bucket:
                    bucket = last_error or "error: unknown"
                
                # Update the exact row in the dataframe
                df_results.at[row_index, 'bucket'] = bucket
                
                # Save immediately after each result
                save_results_live(output_file, df_results, col_name)
                
                with print_lock:
                    completed = df_results['bucket'].notna().sum()
                    if bucket.startswith('error:'):
                        print(f"[Worker {worker_id}] [{completed}/{total_count}] ❌ Row {row_index+1}: {bucket.upper()} ({elapsed:.1f}s)")
                    else:
                        print(f"[Worker {worker_id}] [{completed}/{total_count}] ✅ Row {row_index+1}: {bucket.upper()} ({elapsed:.1f}s)")
                    print("")  # Blank line for readability
                
            except Exception as e:
                if task:
                    row_index, url = task
                    # Final catch-all error
                    df_results.at[row_index, 'bucket'] = f'error: {str(e)[:50]}'
                    save_results_live(output_file, df_results, col_name)
                    
                    with print_lock:
                        print(f"[Worker {worker_id}] ❌ Row {row_index+1}: {url} → FATAL ERROR: {e}")
                        print("")
            finally:
                if task:
                    task_queue.task_done()
    
    finally:
        classifier.close()

def process_urls(input_file, output_file, num_workers=1, headless=True, api_key=None):
    """Process URLs from input CSV and save results to output CSV"""
    
    print(f"\n{'='*70}")
    print(f"  Pagination Classifier")
    print(f"{'='*70}")
    print(f"  Input:    {input_file}")
    print(f"  Output:   {output_file}")
    print(f"  Workers:  {num_workers}")
    print(f"  Headless: {'Yes (browser hidden)' if headless else 'No (browser visible)'}")
    print(f"  AI Judge: {'✅ ENABLED (OpenAI GPT)' if api_key else '❌ DISABLED (heuristics only)'}")
    print(f"{'='*70}\n")
    
    # Read input CSV
    try:
        df = pd.read_csv(input_file)
    except Exception as e:
        print(f"ERROR: Cannot read input file: {e}")
        return
    
    # Find URL column
    col_name = find_url_column(df)
    print(f"Using column '{col_name}' for URLs\n")
    
    # Add bucket column to dataframe (initialize with NaN to preserve row order)
    df['bucket'] = pd.NA
    
    # Collect valid URLs to process (but maintain their row indices)
    # Use a set to track unique URLs and avoid duplicates in input
    seen_urls = set()
    tasks = []
    
    for idx, row in df.iterrows():
        url = str(row[col_name]).strip()
        
        if not url or url.lower() == 'nan':
            # Mark invalid rows
            df.at[idx, 'bucket'] = 'invalid_url'
            continue
        
        # Ensure URL has protocol
        if not url.startswith('http'):
            url = 'https://' + url
        
        # Check for duplicates in input file
        if url in seen_urls:
            df.at[idx, 'bucket'] = 'duplicate_in_input'
            print(f"⚠️  Warning: Duplicate URL found at row {idx+1}: {url}")
            continue
        
        seen_urls.add(url)
        tasks.append((idx, url))
    
    if not tasks:
        print("ERROR: No valid URLs found in input file.")
        return
    
    print(f"Found {len(tasks)} unique URLs to process\n")
    print(f"{'='*70}\n")
    
    # Create initial output file with all rows (buckets will be filled in)
    save_results_live(output_file, df, col_name)
    
    # Track which URLs have been processed (for safety)
    processed_urls = set()
    
    # Create task queue - each task will be picked by exactly one worker
    task_queue = Queue()
    for task in tasks:
        task_queue.put(task)
    
    # Add poison pills for workers (one per worker)
    for i in range(num_workers):
        task_queue.put(None)
    
    # Start workers
    start_time = time.time()
    threads = []
    
    for i in range(num_workers):
        t = threading.Thread(
            target=worker, 
            args=(i+1, task_queue, df, output_file, col_name, len(tasks), processed_urls, headless, api_key)
        )
        t.start()
        threads.append(t)
    
    # Wait for all workers to finish
    for t in threads:
        t.join()
    
    total_time = time.time() - start_time
    
    # Final save
    save_results_live(output_file, df, col_name)
    
    # Summary
    successful = df[df['bucket'].notna() & ~df['bucket'].astype(str).str.startswith('error')]['bucket'].count()
    errors = df[df['bucket'].astype(str).str.startswith('error')]['bucket'].count()
    
    print(f"\n{'='*70}")
    print(f"  ✅ COMPLETE!")
    print(f"{'='*70}")
    print(f"  Total URLs:     {len(tasks)}")
    print(f"  Successful:     {successful}")
    print(f"  Errors:         {errors}")
    print(f"  Total Time:     {total_time:.1f}s")
    print(f"  Avg Time/URL:   {total_time/len(tasks):.1f}s")
    print(f"  Output File:    {output_file}")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Classify pagination patterns in career/job listing pages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python app.py --input test.csv --output results.csv
  python app.py --input urls.csv --output output.csv --workers 5
  python app.py -i input.csv -o output.csv -w 3
        '''
    )
    
    parser.add_argument(
        '--input', '-i',
        type=str,
        default='test.csv',
        help='Input CSV file containing URLs (default: test.csv)'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default='output.csv',
        help='Output CSV file for results (default: output.csv)'
    )
    
    parser.add_argument(
        '--workers', '-w',
        type=int,
        default=1,
        help='Number of parallel workers (default: 1)'
    )
    
    parser.add_argument(
        '--headless',
        action='store_true',
        default=True,
        help='Run browser in headless mode (default)'
    )
    
    parser.add_argument(
        '--no-headless',
        action='store_false',
        dest='headless',
        help='Show browser window (for debugging)'
    )
    
    parser.add_argument(
        '--api-key',
        type=str,
        default=None,
        help='OpenAI API key for AI Judge (optional, can also use OPENAI_API_KEY env var)'
    )
    
    args = parser.parse_args()
    
    # Check for API key from environment variable if not provided
    if not args.api_key:
        import os
        # Try multiple environment variables
        args.api_key = os.getenv('OPENAI_API_KEY') or os.getenv('OUTSPARK_OPENAI_STAGING_API_KEY')
    
    # Validate API key - ignore placeholder values
    if args.api_key and ('your_' in args.api_key.lower() or 'xxxxx' in args.api_key.lower() or len(args.api_key) < 20):
        args.api_key = None  # Treat placeholder as no API key
    
    # Validate inputs
    if not os.path.exists(args.input):
        print(f"ERROR: Input file '{args.input}' not found.")
        exit(1)
    
    if args.workers < 1:
        print(f"ERROR: Number of workers must be at least 1.")
        exit(1)
    
    # Process URLs
    process_urls(args.input, args.output, args.workers, args.headless, args.api_key)
