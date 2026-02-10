# Error Handling & Retry Logic

## Overview

The pagination classifier now includes robust error handling with automatic retries and AI Judge fallback.

## Retry Strategy

### Configuration
- **Max Retries**: 3 attempts per URL
- **Retry Delay**: 3 seconds between attempts
- **Timeout Recovery**: Attempts to proceed with partial page load

### Retry Flow

```
Attempt 1: Process URL
    ↓
   Error?
    ↓
  Yes → Wait 3s → Attempt 2
    ↓
   Error?
    ↓
  Yes → Wait 3s → Attempt 3
    ↓
   Error?
    ↓
  Yes → Check for API Key
    ↓
    ├─ API Key Available → 🤖 AI Judge Fallback
    │                      (Analyzes partial page)
    │                      Returns best guess
    │
    └─ No API Key → ❌ Return error: timeout/failed
```

## Error Types Handled

### 1. Timeout Errors
```
error: timeout
error: Timed out receiving message from renderer
```

**Handling:**
- Stop page loading with `window.stop()`
- Proceed with partial DOM
- Retry up to 3 times
- Fall back to AI Judge if available

**Example:**
```
[Worker 1] ⚠️  Attempt 1/3 failed: error: timeout
[Worker 1] 🔄 Retrying in 3 seconds...
[Worker 1] ⚠️  Attempt 2/3 failed: error: timeout
[Worker 1] 🔄 Retrying in 3 seconds...
[Worker 1] 🤖 All retries failed. Using AI Judge as fallback...
```

### 2. Page Load Failures
```
error: page_load_failed
error: invalid_url
error: dns_probe_finished_nxdomain
```

**Handling:**
- Retry with fresh driver
- Check DNS resolution
- Validate URL format
- Fall back to AI Judge if available

### 3. Driver Crashes
```
error: driver_crashed
error: renderer crashed
```

**Handling:**
- Attempt driver recovery
- Reinitialize if needed
- Retry with new instance
- Fall back to AI Judge if available

### 4. Network Errors
```
error: network_failure
error: connection_refused
```

**Handling:**
- Wait and retry
- Check connectivity
- Use exponential backoff
- Fall back to AI Judge if available

## AI Judge Fallback

### When Used
- ✅ After 3 failed retry attempts
- ✅ API key is configured
- ✅ Timeout/error occurred
- ✅ Partial page load succeeded

### When NOT Used
- ❌ No API key configured
- ❌ URL is completely invalid
- ❌ Driver completely crashed
- ❌ First or second attempt (still retrying)

### AI Judge Input
```python
{
    "url": "https://example.com/careers",
    "error": "timeout",
    "partial_html": "...",  # Whatever was loaded
    "detected_signals": [...],  # Heuristic findings
    "attempt": 3  # Failed attempts
}
```

### AI Judge Output
```python
{
    "bucket": "next",  # Best guess based on partial data
    "confidence": "medium",  # or "low" for timeouts
    "reasoning": "Detected Next button in partial HTML"
}
```

## Output Format

### Success After Retry
```csv
companyUrl, bucket
https://example.com/careers, next
```

### Error (No AI Judge)
```csv
companyUrl, bucket
https://example.com/careers, error: timeout
```

### Error (With AI Judge Fallback)
```csv
companyUrl, bucket
https://example.com/careers, next
```
*Note: AI Judge provided best guess despite errors*

## Terminal Output Examples

### Scenario 1: Success on First Try
```
[Worker 1] [1/10] 🔄 Processing Row 1: https://example.com/careers
  ⏳ Waiting 3 sec for https://example.com/careers to get stabilize...
[Worker 1] [1/10] ✅ Row 1: NEXT (12.3s)
```

### Scenario 2: Success After Retry
```
[Worker 2] [2/10] 🔄 Processing Row 2: https://timeout-site.com/jobs
  ⏳ Waiting 3 sec for https://timeout-site.com/jobs to get stabilize...
  ⚠️  Warning: Timeout occurred, stopped loading and proceeding with partial page...
[Worker 2] ⚠️  Attempt 1/3 failed: error: timeout
[Worker 2] 🔄 Retrying in 3 seconds...
  ⏳ Waiting 3 sec for https://timeout-site.com/jobs to get stabilize...
[Worker 2] [2/10] ✅ Row 2: LOADMORE (25.8s)
```

### Scenario 3: AI Judge Fallback (With API Key)
```
[Worker 3] [3/10] 🔄 Processing Row 3: https://slow-site.com/careers
  ⏳ Waiting 3 sec for https://slow-site.com/careers to get stabilize...
  ⚠️  Warning: Renderer timeout - attempting recovery with partial page...
[Worker 3] ⚠️  Attempt 1/3 failed: error: timeout
[Worker 3] 🔄 Retrying in 3 seconds...
[Worker 3] ⚠️  Attempt 2/3 failed: error: timeout
[Worker 3] 🔄 Retrying in 3 seconds...
[Worker 3] ⚠️  Attempt 3/3 failed: error: timeout
[Worker 3] 🤖 All retries failed. Using AI Judge as fallback...
[Worker 3] [3/10] ✅ Row 3: PAGESELECT (42.1s)
```

### Scenario 4: Final Error (No API Key)
```
[Worker 1] [4/10] 🔄 Processing Row 4: https://broken-site.com/jobs
  ⏳ Waiting 3 sec for https://broken-site.com/jobs to get stabilize...
[Worker 1] ⚠️  Attempt 1/3 exception: timeout
[Worker 1] 🔄 Retrying in 3 seconds...
[Worker 1] ⚠️  Attempt 2/3 exception: timeout
[Worker 1] 🔄 Retrying in 3 seconds...
[Worker 1] ⚠️  Attempt 3/3 exception: timeout
[Worker 1] [4/10] ❌ Row 4: ERROR: TIMEOUT (36.5s)
```

## Configuration

### Enable AI Judge Fallback
```bash
# Set API key
export OPENAI_API_KEY="your_key_here"

# Run with API key
python app.py -i test.csv -o output.csv -w 3
```

### Disable AI Judge Fallback
```bash
# Run without API key (errors will remain as errors)
python app.py -i test.csv -o output.csv -w 3
```

## Best Practices

1. **Use API Key for Production**: Enables AI fallback for difficult sites
2. **Adjust Workers**: More workers = more parallel processing, but more resource usage
3. **Monitor Errors**: Check output.csv for `error:` entries
4. **Retry Manual**: Re-run failed URLs with `--no-headless` to debug
5. **Timeout Sites**: Some sites are just slow - AI Judge helps here

## Statistics

After processing, you'll see:
```
======================================================================
  ✅ COMPLETE!
======================================================================
  Total URLs:     100
  Successful:     92
  Errors:         8
  Total Time:     856.3s
  Avg Time/URL:   8.6s
  Output File:    output.csv
======================================================================
```

Errors of 8% or less are normal for internet scraping!

