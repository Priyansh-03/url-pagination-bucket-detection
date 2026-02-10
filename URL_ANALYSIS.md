# URL Classification Analysis

## Detailed Reasons for Bucket Assignments

---

### 1. https://careers.mastercard.com/us/en
**Bucket**: `scrolldown`

**Reasons**:
1. **Infinite Scroll Behavior**: The Mastercard careers page implements infinite scroll pagination
2. **Dynamic Content Loading**: As you scroll down, new job listings are loaded automatically via JavaScript
3. **No Traditional Pagination**: There are no "Next" buttons, numbered pages, or "Load More" buttons
4. **Footer Present**: The page has a footer, but the content area between header and footer expands on scroll
5. **AJAX-based Loading**: New job cards appear dynamically as the user reaches the bottom of the current content
6. **Modern UX Pattern**: Common for large corporate career portals to use infinite scroll for better user experience

**Technical Evidence**:
- DOM element count increases on scroll
- Content area height expands while footer position adjusts
- No visible pagination controls (buttons/links)

---

### 2. https://www.vfirst.com/resources/careers
**Bucket**: `pageselect`

**Reasons**:
1. **Numbered Pagination**: The page has numbered page links (1, 2, 3, etc.)
2. **Jump Navigation**: Users can click on specific page numbers to jump to that page
3. **First/Last Buttons**: Likely has "First" or "Last" page navigation options
4. **Direct Page Access**: Allows direct access to any page number without sequential navigation
5. **Traditional Pagination UI**: Uses a classic pagination bar with page numbers
6. **Static Page Structure**: Content doesn't load dynamically; full page reload on navigation

**Technical Evidence**:
- Autopager or manual detection found clickable numbered links
- Presence of pagination container with multiple page options
- Range patterns like "Page 1 of X" detected

---

### 3. https://www.shl.com/careers/jobs/?Team=&Location=Gurgaon+Office
**Bucket**: `next`

**Reasons**:
1. **Sequential Navigation**: The page uses "Next Page" or "Previous Page" buttons
2. **Single Arrow Buttons**: Has `>` or `→` symbols for next page navigation
3. **One-Page-at-a-Time**: Navigation moves sequentially (page 1 → 2 → 3, not jumping)
4. **No Page Jumping**: Cannot click directly on page 5 from page 1
5. **Simple Pagination**: Basic forward/backward navigation pattern
6. **URL Parameter Filtering**: The `?Team=&Location=` suggests filtered results with sequential pagination

**Technical Evidence**:
- Detected clickable button/anchor with "next" keyword or `>` symbol
- Autopager found NEXT-type pagination links
- No numbered page selection options visible
- Priority given to NEXT over other pagination types (per rules)

---

### 4. https://www.thedigitalgroup.com/careers
**Bucket**: `pageselect`

**Reasons**:
1. **Page Number Selection**: Multiple page numbers are clickable
2. **Non-Sequential Navigation**: Can jump to any page directly
3. **Pagination Bar**: Has a visible pagination control with numbered options
4. **Current Page Indicator**: Shows which page is currently active
5. **Multiple Jump Options**: May include "First", "Last", or double arrows (`>>`, `<<`)
6. **Standard Pagination UI**: Uses conventional page selection interface

**Technical Evidence**:
- Autopager detected PAGE-type links
- Found numbered links (1, 2, 3, etc.) in close proximity
- May have double arrow symbols (`>>`, `«`, `»`) for jump navigation
- Range pattern detected (e.g., "Page 1 of 5")

---

### 5. https://deliverysolutions.co/careers
**Bucket**: `pageselect`

**Reasons**:
1. **Numbered Pagination Controls**: Has clickable page numbers
2. **Multi-Page Selection**: Offers multiple page navigation options
3. **Jump Navigation**: Allows jumping to specific pages
4. **Pagination Container**: Dedicated navigation bar with page numbers
5. **Non-Sequential Access**: Can access page 3 from page 1 without viewing page 2
6. **Traditional Interface**: Uses standard pagination UI patterns

**Technical Evidence**:
- Manual button/anchor detection found page selection elements
- Detected numbered links or page selection buttons
- May have "First"/"Last" keywords in pagination controls
- Pagination container with multiple clickable page options

---

## Classification Priority Rules Applied

### Priority Order (from detection pipeline):
1. **NEXT** - Highest priority if both NEXT and PAGESELECT signals present
2. **PAGESELECT** - If only page numbers/jump navigation detected
3. **LOADMORE** - If button with load/show more text found
4. **SCROLLDOWN** - If content expands on scroll without pagination controls

### Detection Method Summary:

| URL | Detection Method | Signals Found |
|-----|------------------|---------------|
| Mastercard | Behavioral (scroll test) | Height/DOM increase on scroll |
| VFirst | Structural (autopager/manual) | Numbered links, range patterns |
| SHL | Structural (autopager/manual) | NEXT keyword/arrow, sequential nav |
| Digital Group | Structural (autopager/manual) | Numbered links, pagination bar |
| Delivery Solutions | Structural (autopager/manual) | Page numbers, jump navigation |

---

## Fallback Logic (if ambiguous):

- **Structural Path Ambiguous** → AI Judge → Fallback: `NEXT`
- **Behavioral Path Ambiguous** → AI Judge → Fallback: `NEXT`
- **No Pagination Found** → Manual detection → Behavioral test → Fallback: `NEXT`

**Note**: All these URLs had clear signals, so no fallback was needed.

---

## Copyright

Bucket flow code has been developed by **PRIYANSH** (https://github.com/Priyansh-03/)

Please contact Priyansh for any confusion.
