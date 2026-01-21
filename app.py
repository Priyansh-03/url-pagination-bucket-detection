from flask import Flask, render_template, jsonify, request
import pandas as pd
import os
import threading
import time
from classifier import PaginationClassifier

app = Flask(__name__)
IO_LOCK = threading.Lock()

INPUT_FILE = 'priyansh - test.csv'
OUTPUT_FILE = 'output.csv'

# Ensure output file exists
if not os.path.exists(OUTPUT_FILE):
    pd.DataFrame(columns=['companyUrl', 'bucket', 'reason']).to_csv(OUTPUT_FILE, index=False)

# Global status tracking
class ProcessingManager:
    def __init__(self):
        self.is_running = False
        self.current_url = ""
        self.progress = 0
        self.total = 0
        self.logs = []
        self.stop_requested = False

manager = ProcessingManager()

def read_data():
    with IO_LOCK:
        try:
            # Read Input
            df_in = pd.read_csv(INPUT_FILE)
            
            # Read Original -> Dictionary for fast/robust lookup
            original_file = 'orignal.csv'
            flow_map = {}
            if os.path.exists(original_file):
                 try:
                     df_orig = pd.read_csv(original_file)
                     # Find URL and Flow columns
                     orig_url_col = None
                     orig_flow_col = None
                     
                     for c in df_orig.columns:
                         if 'url' in c.lower() or 'link' in c.lower(): orig_url_col = c
                         if c.lower().strip() == 'flow': orig_flow_col = c
                     
                     if orig_url_col and orig_flow_col:
                         for _, r in df_orig.iterrows():
                             u = str(r[orig_url_col]).strip()
                             f = str(r[orig_flow_col]).strip()
                             if f.lower() == 'nan': f = ''
                             flow_map[u] = f
                             # Also store without trailing slash if present
                             if u.endswith('/'):
                                 flow_map[u[:-1]] = f
                 except Exception as e:
                     print(f"Error reading original: {e}")

            # Read Output/Predicted -> Dictionary
            output_map = {}
            if os.path.exists(OUTPUT_FILE):
                try:
                    df_out = pd.read_csv(OUTPUT_FILE)
                    if 'companyUrl' in df_out.columns and 'bucket' in df_out.columns:
                        for _, r in df_out.iterrows():
                             u = str(r['companyUrl']).strip()
                             b = str(r['bucket']).strip()
                             if b.lower() == 'nan': b = ''
                             output_map[u] = b
                except: pass
        except:
            return []

    # Identify URL column in Input
    col_name = None
    possible_cols = ['companyUrl', 'url', 'link', 'Website', 'career_page_url', 'Website URL']
    for col in possible_cols:
        if col in df_in.columns:
            col_name = col
            break
    
    if not col_name:
         # Try case-insensitive
         for col in df_in.columns:
             if col.lower() in [c.lower() for c in possible_cols]:
                 col_name = col
                 break
                 
    if not col_name:
         for col in df_in.columns:
            if df_in[col].astype(str).str.contains('http').any():
                col_name = col
                break
    col_name = col_name or df_in.columns[0]

    data = []
    for idx, row in df_in.iterrows():
        url = str(row[col_name]).strip()
        if not url or url.lower() == 'nan': continue
        
        # 1. Get Predicted
        bucket = output_map.get(url, '')
        
        # 2. Get Original
        original_flow = flow_map.get(url, '')
        
        # Retry with trailing slash mismatch?
        if not original_flow:
             if url.endswith('/'): original_flow = flow_map.get(url[:-1], '')
             else: original_flow = flow_map.get(url + '/', '')

        data.append({
            'id': idx,
            'url': url,
            'bucket': bucket,
            'original_flow': original_flow,
            'reason': ''
        })
    return data

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    return jsonify(read_data())

@app.route('/api/status')
def get_status():
    return jsonify({
        'is_running': manager.is_running,
        'current_url': manager.current_url,
        'logs': manager.logs[-10:] # Last 10 logs
    })

def run_classifier_thread(urls_to_process):
    manager.is_running = True
    manager.stop_requested = False
    
    classifier = PaginationClassifier()
    
    try:
        # Load current output
        with IO_LOCK:
            if os.path.exists(OUTPUT_FILE):
                df_out = pd.read_csv(OUTPUT_FILE)
            else:
                df_out = pd.DataFrame(columns=['companyUrl', 'bucket'])

        total = len(urls_to_process)
        
        for i, url in enumerate(urls_to_process):
            if manager.stop_requested:
                manager.logs.append("Stopped by user.")
                break
                
            manager.current_url = url
            manager.logs.append(f"Processing: {url}")
            
            # Run classification
            if not url.startswith('http'): url = 'https://' + url
            
            try:
                bucket, reason_text = classifier.classify_url(url)
                manager.logs.append(f"Result: {bucket.upper()}")
                
                # Update DataFrame immediately (inefficient but safe for "don't waste time")
                with IO_LOCK:
                    # Check if exists, update or append
                    if 'companyUrl' in df_out.columns:
                        mask = df_out['companyUrl'] == url
                        if mask.any():
                            df_out.loc[mask, 'bucket'] = bucket
                        else:
                            new_row = pd.DataFrame([{'companyUrl': url, 'bucket': bucket}])
                            df_out = pd.concat([df_out, new_row], ignore_index=True)
                    else:
                         # Initialize if empty/wrong
                         df_out = pd.DataFrame([{'companyUrl': url, 'bucket': bucket}])
                    
                    df_out.to_csv(OUTPUT_FILE, index=False)
                    
            except Exception as e:
                manager.logs.append(f"Error: {e}")
            
    except Exception as e:
        manager.logs.append(f"Fatal Thread Error: {e}")
    finally:
        classifier.close()
        manager.is_running = False
        manager.current_url = ""

@app.route('/api/run', methods=['POST'])
def run_urls():
    if manager.is_running:
        return jsonify({'error': 'Already running'}), 400
        
    data = request.json
    urls = data.get('urls', [])
    
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400
        
    thread = threading.Thread(target=run_classifier_thread, args=(urls,))
    thread.start()
    
    return jsonify({'status': 'Started', 'count': len(urls)})

@app.route('/api/stop', methods=['POST'])
def stop_run():
    if manager.is_running:
        manager.stop_requested = True
        return jsonify({'status': 'Stopping...'})
    return jsonify({'status': 'Not running'})

@app.route('/api/update', methods=['POST'])
def update_row():
    # Manual update from UI
    data = request.json
    url = data.get('url')
    bucket = data.get('bucket')
    
    with IO_LOCK:
        if os.path.exists(OUTPUT_FILE):
            df_out = pd.read_csv(OUTPUT_FILE)
        else:
            df_out = pd.DataFrame(columns=['companyUrl', 'bucket'])
            
        if 'companyUrl' in df_out.columns:
            mask = df_out['companyUrl'] == url
            if mask.any():
                df_out.loc[mask, 'bucket'] = bucket
            else:
                 new_row = pd.DataFrame([{'companyUrl': url, 'bucket': bucket}])
                 df_out = pd.concat([df_out, new_row], ignore_index=True)
            df_out.to_csv(OUTPUT_FILE, index=False)
            
    return jsonify({'status': 'Updated'})

if __name__ == '__main__':
    app.run(debug=True, port=5001, use_reloader=False)
