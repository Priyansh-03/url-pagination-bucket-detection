# Pagination Detection Rules

## Core Principle: Button and Anchor-Only Detection

**CRITICAL RULE**: NEXT and PAGESELECT patterns are ONLY detected in:
- `<button>` elements
- `<a>` elements with valid `href` attributes (not `javascript:void` or `#`)

**Plain text is IGNORED** - only clickable interactive elements are considered.

---

## Detection Categories

### 1. NEXT (Sequential Navigation)

**Purpose**: Detect "Next Page" or "Previous Page" buttons for sequential pagination.

**Patterns Detected**:

#### Keywords (case-insensitive):
- `next`
- `next page`
- `previous` / `prev`

#### Single Arrow Symbols:
- `>` (right arrow)
- `<` (left arrow)
- `→` (right arrow)
- `←` (left arrow)
- `›` (single right angle)
- `‹` (single left angle)
- `➜` (right arrow)

**XPath Pattern**:
```xpath
//button[...pattern...] | //a[@href and ...pattern...]
```

**Priority**: NEXT detection takes priority over PAGESELECT if both patterns exist.

---

### 2. PAGESELECT (Jump Navigation)

**Purpose**: Detect numbered page selection or jump-to-page functionality.

**Patterns Detected**:

#### Keywords (case-insensitive):
- `first`
- `last`

#### Double Arrow Symbols:
- `>>` (double right arrow)
- `<<` (double left arrow)
- `»` (right guillemet)
- `«` (left guillemet)

#### Numbered Links:
- Clickable numbers: `1`, `2`, `3`, `4`, `5`
- Must have at least 2 numbered links present
- Links must be in close proximity (same container)

**XPath Pattern**:
```xpath
//button[...pattern...] | //a[@href and ...pattern...]
```

**Range Patterns** (text-based, not button-specific):
- "Page 1 of 10"
- "Results 1-20 of 100"
- "Items per page"
- "Jump to page"

---

### 3. LOADMORE (Expansion Buttons)

**Purpose**: Detect buttons that load more content without navigation.

**Patterns Detected** (case-insensitive):

#### Generic Patterns:
- `load more`
- `show more`
- `view more`
- `see more`
- `load all`
- `show all`
- `view all`
- `see all`
- `more results`
- `load additional`
- `show additional`

#### Job-Specific Patterns:
- `view more jobs`
- `view all jobs`
- `see all jobs`
- `more jobs`
- `all jobs`
- `view jobs`
- `show jobs`
- `load jobs`
- `see jobs`

**Element Types**: `<button>`, `<a>` tags with valid `href`

---

### 4. SCROLLDOWN (Infinite Scroll)

**Purpose**: Detect pages that load content automatically on scroll.

**Detection Logic**:

#### Scenario A: Page with Footer
- **Condition**: Footer element detected (`<footer>`, `#footer`, `.footer`)
- **Rule**: Content **between header and footer** must expand on scroll
- **Method**:
  - Measure content area elements before scroll
  - Scroll to bottom, wait 2-5 seconds
  - Re-measure content area
  - If content elements increased by 5+, classify as `scrolldown`

#### Scenario B: Page without Footer (Infinite Scroll)
- **Condition**: No footer element detected
- **Rule**: Page height must **keep growing** on scroll
- **Method**:
  - Measure page height before scroll
  - Scroll to bottom, wait 2-5 seconds
  - Re-measure page height
  - If height increased by 400+ pixels OR elements increased by 8+, classify as `scrolldown`

**Retry Logic**: Two attempts (2s wait, then 5s wait) to handle slow-loading sites.

---

## Detection Pipeline

```
Page Load
    ↓
Extract HTML & DOM
    ↓
Run Autopager
    ↓
    ├─ Autopager YES → STRUCTURAL PATH
    │   ├─ Check NEXT patterns (priority)
    │   ├─ Check PAGESELECT patterns
    │   ├─ If ambiguous → AI Judge → Fallback: NEXT
    │   └─ Return decision
    │
    └─ Autopager NO → MANUAL BUTTON/ANCHOR DETECTION
        ├─ Check buttons/anchors for NEXT patterns
        ├─ Check buttons/anchors for PAGESELECT patterns
        ├─ Check buttons/anchors for LOADMORE patterns
        ├─ If found → Return decision
        └─ If not found → BEHAVIORAL PATH
            ├─ Test SCROLLDOWN (wait & measure)
            ├─ Test LOADMORE (click button & measure)
            ├─ If ambiguous → AI Judge → Fallback: NEXT
            └─ Return decision
```

---

## Why Button-Only?

**Problem**: Plain text containing pagination keywords (e.g., "Next article", "View more details") can trigger false positives.

**Solution**: Only detect pagination in **clickable elements** (`<button>` and `<a>` tags) that users can actually interact with.

**Example**:
```html
<!-- ❌ IGNORED (plain text) -->
<p>Click next to continue</p>

<!-- ✅ DETECTED (button) -->
<button>Next</button>

<!-- ✅ DETECTED (anchor with href) -->
<a href="/page/2">Next</a>

<!-- ❌ IGNORED (anchor without href) -->
<a>Next</a>

<!-- ❌ IGNORED (javascript void) -->
<a href="javascript:void(0)">Next</a>
```

---

## Fallback Rules

### Structural Path Fallback:
- If Autopager detects pagination but signals are ambiguous
- Try AI Judge (if API key available)
- Final fallback: **NEXT**

### Behavioral Path Fallback:
- If Scrolldown and LoadMore tests fail
- Try AI Judge (if API key available)
- Final fallback: **NEXT**

### No "default" or "none" Buckets:
- All pages must be classified into one of: `next`, `pageselect`, `loadmore`, `scrolldown`
- If truly unknown, fallback is always **NEXT**

---

## Copyright

Bucket flow code has been developed by **PRIYANSH** (https://github.com/Priyansh-03/)

Please contact Priyansh for any confusion.
