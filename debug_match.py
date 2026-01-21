import pandas as pd

def normalize(url):
    if not isinstance(url, str): return ""
    u = url.strip().lower()
    if u.endswith('/'): u = u[:-1]
    return u

try:
    print("--- INPUT FILE (priyansh - test.csv) ---")
    df_in = pd.read_csv('priyansh - test.csv')
    print("Columns:", df_in.columns.tolist())
    # find likely url col
    url_col_in = [c for c in df_in.columns if 'url' in c.lower() or 'link' in c.lower()][0]
    print(f"Using URL col: {url_col_in}")
    print(df_in[url_col_in].head(5).tolist())

    print("\n--- ORIGINAL FILE (orignal.csv) ---")
    df_orig = pd.read_csv('orignal.csv')
    print("Columns:", df_orig.columns.tolist())
    url_col_orig = [c for c in df_orig.columns if 'url' in c.lower() or 'link' in c.lower()][0]
    print(f"Using URL col: {url_col_orig}")
    print(df_orig[url_col_orig].head(5).tolist())
    
    print("\n--- MATCHING CHECK ---")
    msg = []
    count = 0
    for idx, row in df_in.iterrows():
        u_in = str(row[url_col_in])
        
        # Try finding in orig
        # Exact match
        match = df_orig[df_orig[url_col_orig] == u_in]
        
        if not match.empty:
            count += 1
            if count < 3: print(f"Match found for {u_in}")
        else:
            # Check normalized
            # print(f"No exact match for: '{u_in}'")
            # Try strip
            match_strip = df_orig[df_orig[url_col_orig].astype(str).str.strip() == u_in.strip()]
            if not match_strip.empty:
                 if count < 3: print(f"  > Match found via STRIP for {u_in}")
                 count += 1
            else:
                 if count < 5: print(f"  > FAIL: {u_in}")
                 
    print(f"\nTotal matches found: {count} out of {len(df_in)}")

except Exception as e:
    print(e)
