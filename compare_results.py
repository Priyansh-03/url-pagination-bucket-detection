
import pandas as pd
import csv

def compare():
    correct_df = pd.read_csv('correct.txt', sep='\t')
    output_df = pd.read_csv('output_verified.csv')
    
    # Clean up flow column (strip whitespace and convert to lower)
    correct_df['flow'] = correct_df['flow'].astype(str).str.strip().str.lower()
    output_df['flow'] = output_df['flow'].astype(str).str.strip().str.lower()
    
    # Merge on companyUrl
    merged = pd.merge(correct_df[['companyUrl', 'flow']], output_df[['companyUrl', 'flow']], on='companyUrl', suffixes=('_expected', '_actual'))
    
    mismatches = merged[merged['flow_expected'] != merged['flow_actual']]
    print(f"Total mismatches: {len(mismatches)}")
    print("\nMismatches List:")
    for _, row in mismatches.iterrows():
        print(f"URL: {row['companyUrl']}")
        print(f"  Expected: {row['flow_expected']}")
        print(f"  Actual:   {row['flow_actual']}")
        print("-" * 50)

if __name__ == "__main__":
    compare()
