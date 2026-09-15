# DNR Special Local Watercraft Controls: crawl recon

Recon for the `fetch` / `parse-dnr` pipeline stages (design.md section 4/5, data-contract.md
`restrictions.jsonl`). Findings only; no parser code here. Fetched 2026-09-15 with a browser
User-Agent (default curl UA gets a 403 — see Gotchas). All 83 county pages plus the index were
downloaded and inspected; fixtures for 5 counties + the index are saved under
`pipeline/tests/fixtures/dnr_pages/`.

## 1. Index page and discovery method

**Index URL:** `https://www.michigan.gov/dnr/managing-resources/laws/controls`
(saved as `pipeline/tests/fixtures/dnr_pages/_index.html`, 780,798 bytes).

- `https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols` (no trailing
  segment) returns **404** (Michigan.gov's generic `/dnr/404` page). The real index is one level
  up, at `.../laws/controls`, confirmed via the Oakland page's breadcrumb trail:
  `Home > Managing your resources > Rules, laws and enforcement > Controls > Local Watercraft
  Controls`, where "Controls" links to `.../laws/controls`.
- The index page is a Drupal/Sitecore-style **accordion**, one `<li class="item accordion-item">`
  per county. Each item has:
  - `<h2 class="accordion-item__heading field-heading">COUNTY NAME</h2>` (title case, e.g.
    `Grand Traverse`, `St. Clair`, `St. Joseph`, `Van Buren`, `Presque Isle` — note the spaces,
    unlike the URL slugs which strip them).
  - A collapsed `<div class="toggle-content accordion-item__content">` containing
    `<ul><li><a href="...">Watercraft</a></li><li><a href="...">Hunting</a></li></ul>` (Oakland
    and Emmet say `"Local Watercraft Controls"` instead of `"Watercraft"` as the link text).
  - The accordion is collapsed by CSS/JS in the browser (`aria-expanded="false"`), but **all 83
    items and both links are present in the raw server-rendered HTML** — a plain `curl` (no JS
    execution) retrieves the complete list. This is not a JS-rendered/AJAX page.
- Extraction method used: regex-split the HTML on the `accordion-item__heading` markers to get
  83 (county, [links]) chunks, then pull the `localcontrols/<slug>/...` href that is not
  `.../hunting`.
- **Verified count: exactly 83 accordion items, each with exactly one watercraft link and one
  hunting link** — matches Michigan's 83 counties one-to-one. No separate letter-index, dropdown,
  sitemap, or JSON endpoint exists; this single accordion page is the entire mechanism.

### Slug pattern and anomalies

Pattern: `https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/<slug>/watercraft`,
where `<slug>` is the county name lowercased with spaces and punctuation stripped
(`grandtraverse`, `stclair`, `stjoseph`, `vanburen`, `presqueisle`).

Exactly **two exceptions** to the `.../watercraft` tail, both discoverable only by reading the
index page's actual hrefs (do not hardcode a guessed slug):

| County | URL tail |
|---|---|
| Oakland | `.../oakland/local-watercraft-controls` |
| Emmet | `.../emmet/local-watercraft-controls` |

All other 81 counties use `.../<slug>/watercraft`. **Conclusion for `fetch`: always resolve URLs
from the index page's hrefs; never construct them from a county-name pattern.**

## 2. Full county -> URL table (83 of 83)

All verified HTTP 200 on 2026-09-15 with a browser UA.

| County | URL |
|---|---|
| Alcona | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/alcona/watercraft |
| Alger | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/alger/watercraft |
| Allegan | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/allegan/watercraft |
| Alpena | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/alpena/watercraft |
| Antrim | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/antrim/watercraft |
| Arenac | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/arenac/watercraft |
| Baraga | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/baraga/watercraft |
| Barry | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/barry/watercraft |
| Bay | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/bay/watercraft |
| Benzie | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/benzie/watercraft |
| Berrien | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/berrien/watercraft |
| Branch | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/branch/watercraft |
| Calhoun | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/calhoun/watercraft |
| Cass | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/cass/watercraft |
| Charlevoix | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/charlevoix/watercraft |
| Cheboygan | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/cheboygan/watercraft |
| Chippewa | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/chippewa/watercraft |
| Clare | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/clare/watercraft |
| Clinton | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/clinton/watercraft |
| Crawford | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/crawford/watercraft |
| Delta | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/delta/watercraft |
| Dickinson | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/dickinson/watercraft |
| Eaton | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/eaton/watercraft |
| Emmet | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/emmet/local-watercraft-controls |
| Genesee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/genesee/watercraft |
| Gladwin | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/gladwin/watercraft |
| Gogebic | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/gogebic/watercraft |
| Grand Traverse | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/grandtraverse/watercraft |
| Gratiot | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/gratiot/watercraft |
| Hillsdale | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/hillsdale/watercraft |
| Houghton | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/houghton/watercraft |
| Huron | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/huron/watercraft |
| Ingham | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/ingham/watercraft |
| Ionia | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/ionia/watercraft |
| Iosco | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/iosco/watercraft |
| Iron | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/iron/watercraft |
| Isabella | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/isabella/watercraft |
| Jackson | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/jackson/watercraft |
| Kalamazoo | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/kalamazoo/watercraft |
| Kalkaska | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/kalkaska/watercraft |
| Kent | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/kent/watercraft |
| Keweenaw | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/keweenaw/watercraft |
| Lake | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/lake/watercraft |
| Lapeer | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/lapeer/watercraft |
| Leelanau | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/leelanau/watercraft |
| Lenawee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/lenawee/watercraft |
| Livingston | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/livingston/watercraft |
| Luce | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/luce/watercraft |
| Mackinac | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/mackinac/watercraft |
| Macomb | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/macomb/watercraft |
| Manistee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/manistee/watercraft |
| Marquette | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/marquette/watercraft |
| Mason | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/mason/watercraft |
| Mecosta | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/mecosta/watercraft |
| Menominee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/menominee/watercraft |
| Midland | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/midland/watercraft |
| Missaukee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/missaukee/watercraft |
| Monroe | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/monroe/watercraft |
| Montcalm | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/montcalm/watercraft |
| Montmorency | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/montmorency/watercraft |
| Muskegon | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/muskegon/watercraft |
| Newaygo | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/newaygo/watercraft |
| Oakland | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oakland/local-watercraft-controls |
| Oceana | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oceana/watercraft |
| Ogemaw | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/ogemaw/watercraft |
| Ontonagon | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/ontonagon/watercraft |
| Osceola | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/osceola/watercraft |
| Oscoda | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oscoda/watercraft |
| Otsego | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/otsego/watercraft |
| Ottawa | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/ottawa/watercraft |
| Presque Isle | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/presqueisle/watercraft |
| Roscommon | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/roscommon/watercraft |
| Saginaw | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/saginaw/watercraft |
| Sanilac | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/sanilac/watercraft |
| Schoolcraft | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/schoolcraft/watercraft |
| Shiawassee | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/shiawassee/watercraft |
| St. Clair | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/stclair/watercraft |
| St. Joseph | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/stjoseph/watercraft |
| Tuscola | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/tuscola/watercraft |
| Van Buren | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/vanburen/watercraft |
| Washtenaw | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/washtenaw/watercraft |
| Wayne | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/wayne/watercraft |
| Wexford | https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/wexford/watercraft |

### Counties with no controls

7 of 83 county pages render only a "no controls" placeholder (verified by fetching and grepping
all 83 for the phrase below): **Gratiot, Houghton, Ingham, Ionia, Missaukee, Sanilac, Shiawassee**.
The `parse-dnr` stage should treat a page whose body matches `/No Special Local Watercraft
Controls/i` as "zero restrictions, not a fetch failure" rather than retrying or flagging an error.

Missaukee's exact body (see section 3 for markup): `-- No Special Local Watercraft Controls in
This County --`.

## 3. HTML structure

### Container

Every county watercraft page (controls or no-controls) puts its content in the same container:

```html
<section class="component component-wrapper section__pagebody" id="pagebody">
  <div class="component content component__section field__content">
    <div class="component-content"><div class="container"><div class="row"><div class="col-12">
      <div class="field-content"> ... the actual content lives here ... </div>
    </div></div></div>
  </div>
</section>
```

`div.field-content` is the target node. It is a single Drupal WYSIWYG field dumped as raw
`<h2>`/`<p>` markup — there are no per-lake `<div>`/`<section>`/`<table>` wrappers, no
`<dl>`/`<dt>`/`<dd>`, and no `data-*` attributes marking record boundaries. **Entries are
delimited only by a bold inline tag inside a `<p>`.**

### Page heading (inconsistent tag)

Almost every county opens with:
```html
<h2>SPECIAL LOCAL WATERCRAFT CONTROLS - OAKLAND COUNTY</h2>
```
but the no-controls counties use a plain bold paragraph instead of `<h2>` (Missaukee):
```html
<div class="field-content"><p><b>SPECIAL LOCAL WATERCRAFT CONTROLS - MISSAUKEE COUNTY</b></p><p>-- No Special Local Watercraft Controls in This County --</p></div>
```
Parser should not depend on `<h2>` being present; use it only to grab the county name for a
sanity check, not as a required record boundary.

### Per-lake / per-rule entry (the record boundary)

Confirmed on Oakland, Cheboygan, Kent, Livingston, Wayne, Kalkaska, and 10+ other counties. The
repeating unit is:

```html
<p><strong>LAKE NAME - RULE_ID - Short title.</strong></p>
<p>Body paragraph 1 (often starts with the bare rule number, "62." or "Rule 62.", then the
   operative sentence, sometimes ending mid-clause with "(a) ...").</p>
<p>(b) Additional clause, if any — sub-clauses (b), (c), (d)... are frequently their OWN <p>,
   not part of the same paragraph as (a).</p>
<p>History: Eff. <date>.</p>
```

Real example (Oakland, verbatim from the fetched HTML):
```html
<p><strong>BIG AND LITTLE SCHOOL LOT LAKES AND CONNECTING CHANNEL - R281.763.3 - High-speed boating and water skiing prohibited.</strong></p>
<p>3. On the waters of Big School Lot lake, Little School Lot lake, and the waters of the channel connecting Big School Lot lake and Little School Lot lake, section 16, town 4 north, range 7 east, township of Rose, county of Oakland, state of Michigan, no operator of any motorboat shall:&nbsp;(a) Operate such motorboat at high speed, which means a speed at or above which a motorboat reaches a planning condition.</p>
<p>(b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, surfboard, or other similar contrivance.</p>
<p>History: Eff. May 18, 1970</p>
```

**Boundary-marking tag varies by county/page — this is the single most important parser
gotcha:**

| County (fixture) | Header tag used |
|---|---|
| Oakland, Kent, Livingston | `<strong>` |
| Cheboygan | `<b>` |
| Missaukee (no-controls placeholder) | `<b>` |

A regex/BeautifulSoup rule must match **both** `<strong>` and `<b>` as the record-boundary tag.
`<p>` tags themselves sometimes carry a stray `align="justify"` attribute (Kent) — irrelevant to
text but shows the markup is hand-pasted from Word/PDF, not templated consistently.

One county page (Kent) additionally has literal blank lines between every `<p>` in the raw HTML
(`</p>\n\n<p>`), while others (Oakland, Livingston) have none (`</p>\n<p>`) — cosmetic only, but
confirms whitespace must be normalized rather than relied on for structure.

**Multi-clause entries are one record with several restriction_type-worthy clauses.** E.g. Cass
Lake (Oakland, R281.763.36) has clause (a) an hours-windowed high-speed/ski ban, clause (b) a flat
50 mph speed limit, and clause (c) a zone-scoped slow-no-wake — all three are legally independent
restrictions attached to the *same* `<strong>` header and rule number. The `parse-dnr` splitter
must walk `(a)`/`(b)`/`(c)`/`(1)`/`(2)`/`(3)` sub-clauses inside one entry and may emit more than
one `restrictions.jsonl` row per entry, all sharing the same `rule_id`/`lake_name_raw` but with
different `raw_text` (or a shared `raw_text` with `restriction_type` as a list — pick one and
apply consistently).

**Rescinded entries can omit the lake name from the header entirely.** Real example (Kalkaska):
```html
<p><strong>WC-40-91-002 - Rescinded: February 24, 2009 </strong></p>
<p>Publisher's Note: This Watercraft order was for Manistee Lake, Coldsprings township, Kalkaska county</p>
```
Here the header is just `RULE_ID - Rescinded: DATE` (no `LAKE NAME -` prefix); the lake name only
exists in the following "Publisher's Note" sentence. A naive `"NAME - RULE - TITLE"` split will
either fail or (worse) mis-assign "WC-40-91-002" as the lake name. Recommendation: detect
`Rescinded` in the title/first line and either skip the entry (it has no active restriction) or
parse the lake name out of the Publisher's Note sentence and mark `needs_review: true`. 14
`Rescinded` mentions found across the 83-county corpus.

### Rule number formats — three schemes, all appearing on the same page

1. **Legacy Admin Code rule**: `R281.763.3` (no space) — by far the most common (672 occurrences
   across all counties). Rarely appears **with** a space: `R 281.758.3`, `R 281.700.3`,
   `R 281.772.9`, `R 281.763.39` (4 occurrences total) — same meaning, inconsistent typography.
   Regex must treat the space as optional: `R\s?281\.\d+\.\d+`.
2. **Modern Watercraft Order**: `WC-63-24-002` — 299 occurrences. Format is
   `WC-<county code>-<2-digit year>-<3-digit sequence>`, e.g. `63` = Oakland, `47` = Livingston,
   `41` = Kent, `16` = Cheboygan. These are director/deputy-director orders issued under
   delegated authority and are **not** part of the codified Administrative Code — they will not
   be found on law.cornell.edu or the LARA Admin Code system (see section 5). Formatting of the
   dashes/spaces is inconsistent even within one page: seen as `WC-63-24-002`,
   `WC - 47 - 01 - 001` (Livingston, spaces around every dash), `WC 11-96-001` (no dash after
   `WC`), `WC - 80-00-001` (mixed). Regex must tolerate optional whitespace around every dash:
   `WC\s?-?\s?\d+\s?-?\s?\d+\s?-?\s?\d+`.
3. **No rule number found in the title at all** for the rescinded-entry edge case above, and
   occasionally the number is followed directly by a period with no space before the title,
   e.g. `R281.763.18. - Slow-no wake speed.` (Cedar Island Lake, Oakland) vs the usual
   `R281.763.18 - Slow-no wake speed.`.

It is normal and expected for the **same lake** to have both an old `R281.x` entry and a newer
`WC-xx` entry as separate, independently-standing records (e.g. Oakland's "FISH LAKE" has both
`R281.763.8` for slow-no-wake and `WC-63-86-003` for electric-motor-only — two different
restrictions, two different rule numbers, same lake name string). Do not assume one rule number
per lake.

### PLSS ("Section 16, T4N, R7E") formats — two spellings, sometimes both in one sentence

- Abbreviated: `section 33, T3N, R8E, White Lake township, Oakland county`
- Spelled out: `section 16, town 2 north, range 8 east, Commerce township, Oakland county`
- Multiple sections: `sections 4, 5, 8, and 9`, `sections 21 and 22`
- No PLSS at all, only township: `township of Orion, county of Oakland, state of Michigan` (no
  section/T/R given — happens on maybe 1 in 10 entries, usually where the whole named waterbody
  is inside one township and DNR didn't bother citing the section).
- Order of T/R can vary: `T29N, R2W` (township then range) is universal in the abbreviated form,
  but the spelled-out form is always `town X north, range Y east` — never reversed.
- **Data-quality note, not a parser bug**: at least one entry (Seymour Lake, Oakland,
  WC-63-90-001) cites `T5N, R9W` for a lake in Oakland County, which is geographically wrong
  (Oakland is Range East of the meridian; `R9W` would be well into western Michigan). This looks
  like a source typo. The `match` stage's PLSS-centroid step should not hard-fail on an
  out-of-county T/R — fall back to name/county matching and flag for review rather than trusting
  the section centroid blindly.

## 4. Restriction-text template catalog

Grouped by `restriction_type` (data-contract.md enum) plus a few extra buckets the schema doesn't
name a slot for. All quotes below are copied verbatim from the fetched HTML (whitespace collapsed,
`&nbsp;`/entities decoded). Counts are raw keyword-occurrence counts across the full 83-county
corpus, not annotated-record counts — a floor, not the true count (e.g. "water ski" alone
matches 500 times because nearly every high-speed/ski clause repeats the phrase).

### `no_motorboats` — motorboats prohibited (lakewide)

- Oakland, Brookfield Pond, R281.763.22: *"it is unlawful to operate a motorboat."*
- Oakland, Cranberry Lake, R281.763.49: *"it is unlawful to operate a motorboat."*
- Oakland, Moon Lake, R281.763.23: *"On the waters of Moon lake, sections 13 and 14, town 2 north, range 9 east, West Bloomfield township, Oakland county, it is unlawful to operate a motorboat."*
- Livingston, Osborne Lake, R281.747.10: *"On the waters of Osborne lake, section 8, town 2 north, range 6 east, Brighton township, Livingston county, it is unlawful to operate a motorboat."*
- Alger, Fox River and tributaries, WC-02-90-001: *"On the waters of the Fox river and its tributaries located within Burt township, Alger county, motorboats are prohibited."*
- **Electric-motor-only variant** (same `no_motorboats` intent, different wording — motorboats
  are prohibited *except* electric): Oakland, Bass Lake, R281.763.62: *"it is unlawful to operate
  a vessel powered by a motor except an electric motor."* — this exact sentence (with only the
  lake/section/township changed) repeats dozens of times across counties; also seen as *"it is
  unlawful to operate a motorboat powered by any motor other tha[n] an electric trolling motor"*
  (Oakland, Sears Lake, WC-63-12-001, with a definition: *"a trolling motor is a portable, battery
  powered motor designed and intended for slow, quiet operation"*).

### `slow_no_wake`, scope lakewide

- Oakland, Fish Lake, R281.763.8: *"it shall be unlawful for an operator of a vessel to exceed a slow-no wake speed, which means a very slow speed whereby the wake or wash created by the vessel would be minimal."*
- Oakland, Chalmers Lake, WC-63-24-002 (modern order template with signage requirement): *"it shall be unlawful for an operator of a vessel to exceed a slow-no wake speed. 'Slow-no wake speed' means a very slow speed whereby the wake or wash created by the vessel would be minimal. The boundaries of the area described above shall be marked with signs and/or with buoys at all points of public entry... It is the responsibility of Bloomfield township to ensure all signage, including any buoys are provided, placed, and maintained to notify boaters of this watercraft control. ... This watercraft control is only enforceable when properly marked."*
- Livingston, Cordley Lake, WC-47-95-001: *"it is unlawful for the operator of a vessel to exceed a slow-no wake speed. The boundaries of the area described immediately above shall be marked with signs and with buoys. All buoys must be placed as provided in a permit issued by the Department of Natural Resources and be in conformance with the State Uniform Waterway Marking System."*
- Kent, Squaw Lake, WC-41-97-001: same template as above.
- Dickinson, Bush Lake, WC-22-99-001: same template.

  **Gotcha:** the "must be marked with signs and/or buoys ... only enforceable when properly
  marked" clause appears 170 times across the corpus — it is boilerplate attached to nearly every
  modern `WC-` order, not a per-lake fact worth extracting into its own field, but it does confirm
  these newer controls are conditionally enforceable (signage-dependent) in a way the old `R281`
  rules are not. Worth a boolean flag if the pipeline wants it, but not required by the schema.

### `slow_no_wake` / `no_high_speed`, scope zone (named sub-area, not the whole lake)

- Oakland, Buckhorn Lake North Basin, R281.763.7: *"...north of Demode road, it is unlawful for the operator of a vessel to exceed a slow-no wake speed."*
- Oakland, Cass Lake, R281.763.36(c): *"To operate a vessel in excess of a slow-no wake speed in the northerly tip of Coles bay, Waterford township, within the north 1/2 of the northwest 1/4 of the southwest 1/4 of section 35, T3N, R9E."*
- Oakland, Deer Lake, R281.763.27 ("Slow-no wake speed zone"): *"north of the south line of the northwest 1/4 of the southeast 1/4, section 19, it is unlawful for the operator of a vessel to exceed a slow-no wake speed."*
- Oakland, Cedar Island Lake certain bays, R281.763.18: six lettered sub-clauses (a)-(f), each a
  different lot-line-to-lot-line boundary description, e.g. *"(a) Southerly and westerly of a
  line beginning where the east line of lot 42, Golden Shores subdivision no. 1, intersects the
  water's edge and thence to the point where the north line of lot 54, Cedar View subdivision, as
  extended, intersects the water's edge."*
- Washtenaw, Base Line Lake Bay, R281.781.7: *"...in the SW 1/4, NW 1/4, and the W 1/2 of the SE 1/4, NW 1/4, section 6..."* (zone described by quarter-quarter-section, not a line).

### `no_high_speed` — high-speed boating (and usually water skiing) prohibited, no hours

- Oakland, Big and Little School Lot Lakes, R281.763.3: *"no operator of any motorboat shall: (a) Operate such motorboat at high speed, which means a speed at or above which a motorboat reaches a planning condition. (b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, surfboard, or other similar contrivance."*
- Oakland, Parke Lake, R281.763.41: *"it is unlawful to: (a) Operate a vessel at high speed. (b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, kite, surfboard, or other similar contrivance."*
- Alcona, Crooked Lake, R281.701.1: title "High-speed boating and water skiing prohibited" (same clause pair as above).
- Gogebic, Imp Lake, R281.727.1: *"no operator of any motorboat shall: (a) Operate such motorboat at high speed, which means a speed at or above which a motorboat reaches a planing condition. (b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, surfboard, or other similar contrivance."*
- Osceola, Center Lake, R281.767.1: identical two-clause template.

### `high_speed_hours` — high speed / skiing allowed except during a window (i.e., prohibited during stated hours)

- Oakland, Deer Lake, R281.763.25: *"it is unlawful, between the hours of 6:30 p.m. and 10:00 a.m. of the following day, to: (a) Operate a vessel at high speed. (b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, kite, surfboard, or other similar contrivance."*
- Oakland, Voorheis Lake, R281.763.14: *"no operator of any motorboat, during the period from 6:30 p.m. to 10:00 a.m. of the following day, shall: (a) Operate such motorboat at high speed... (b) Have in tow..."*
- Oakland, Middle Straits Lake (West Bloomfield part), R281.763.39(2): *"it is unlawful on Sundays, Memorial Day, Independence Day, and Labor Day, except between the hours of 10:00 a.m. and 6:30 p.m., to: (a) Operate a vessel at high speed. (b) Have in tow..."* — hours-window scoped to specific **named holidays/days**, not every day.
- Livingston, Appleton Lake, WC-47-01-001: *"it is unlawful between the hours of 6:30 p.m. to 10:00 a.m. of the following day to: (a) Operate a vessel at high speed. (b) Have in tow... The hours should be 7:30 p.m. to 11:00 a.m. of the following day when Eastern Daylight Savings Time is in effect."* — explicit DST-shifted hour clause, a template variant that appears on several `WC-` orders (also Kent's Olin Lake WC-41-95-002 and Upper Lake WC-41-95-001 use the identical DST sentence).
- Van Buren, Big Crooked Lake, WC-80-90-001: *"it is unlawful between the hours of 6:30 p.m. to 10:00 a.m. of the following day to: (a) Operate a vessel at high speed. (b) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, kite, or other similar contrivance."*
- Kent, Big Wabasis Lake, R281.741.1(a): *"Between the hours of 6:30 p.m. and 10:00 a.m. of the following day, to operate a vessel at high speed, or have in tow, or otherwise assist in the propulsion of, a person on water skis..."* — note (a) and (b) merged into one sentence here rather than split, and (b) at any time adds a flat 35 mph cap (see speed_limit below) — one entry containing both an hours-windowed ban and an absolute numeric limit.

### `speed_limit` — numeric caps

- Oakland, Orchard Lake, R281.763.57 ("Watercraft speed limit"): *"it is unlawful at any time to operate a vessel in excess of 40 miles per hour (64 kilometers per hour)."*
- Oakland, Stringy Lakes (Tan, Clear, Squaw, Second, Spring, Cedar, and Long), R281.763.58: identical *"...in excess of 40 miles per hour (64 kilometers per hour)."*
- Oakland, Cass Lake, R281.763.36(b): *"At any time to operate a vessel in excess of 50 miles per hour (80 kilometers per hour)."*
- Oakland, Lakeville Lake, R281.763.40(3): *"...at a speed in excess of 35 miles per hour (56 kilometers per hour)."*
- Kent, Big Wabasis Lake, R281.741.1(b): *"At any time, to operate a vessel at a speed in excess of 35 miles per hour (56 kilometers per hour)."*
- Kent, Camp Lake, WC-41-86-001: *"it is unlawful at any time to operate a vessel in excess of 40 miles per hour (64 kilometers per hour)."*
- Oakland, Green Lake, WC-63-13-001 (combined with motor restriction): *"it is unlawful to operate a vessel powered by a motor, except an electric motor, which shall not exceed a speed limit of 10 miles per hour. Speed and motor restrictions shall not apply to law enforcement or emergency response vessels."* — note the explicit law-enforcement/emergency carve-out, and that the numeric limit is bolted onto an electric-motor-only rule, not a standalone speed_limit record.

### `no_towing` — water skiing bans / hour restrictions (distinct from "high speed" bans, no motorboat-speed clause)

- Cheboygan, Silver Lake, R281.716.1 ("Hours for water skiing"): *"no operator of any motorboat shall have in tow or shall otherwise be assisting in the propulsion of a person on water skis, water sleds, surfboards, or other similar contrivance during the period from 6:30 p.m. to 10:00 a.m. of the following day."*
- Wexford, Berry Lake, R281.783.1 ("Hours for water skiing"): identical template/wording to Cheboygan's Silver Lake.
- Oakland, Deer Lake, R281.763.26 ("Limitation of water skiers"): *"it is unlawful to tow, or otherwise assist in the propulsion of, more than 2 persons at 1 time on water skis, water sled, kite, surfboard, or other similar contrivance."* — a headcount limit, not a ban or hours window; there is no `restriction_type` for this in the current enum (falls to `other`).
- Cheboygan, Indian River, WC-16-09-001: *"it is unlawful for the operator of a vessel to: (a) Have in tow, or otherwise assist in the propulsion of, a person on water skis, water sled, surfboard, tube, or other similar contrivance between the hours of 10:00 a.m. and 7:00 p.m., on Saturdays and Holidays during the months of June, July and August."* — towing ban scoped to specific days-of-week + a season, a combination the schema's single `hours`/`season` pair does not cleanly capture (needs both simultaneously, plus "Saturdays and Holidays" which is neither).

### `no_pwc` — personal watercraft rules

**None found.** Searched the full 83-county corpus for `"personal watercraft"`, `"PWC"`,
`"jet ski"`, `"jetski"` — zero matches in any of the 83 pages. Michigan's DNR local watercraft
control program appears not to single out PWCs specifically in the current rule set; PWCs are
presumably governed by whatever motorboat/high-speed/no-wake rule already applies to the lake.
**Recommendation:** keep the `no_pwc` enum value for forward-compatibility (the underlying Admin
Code part could add one later) but do not expect the regex pass to ever match it against this
snapshot of the data; treat a hit as noteworthy enough to warrant `needs_review: true`.

### Seasonal rules

- Alger, AuTrain River / Cleveland Cliffs Basin, R281.702.3 ("Seasonal prohibition of
  motorboats"): *"it is unlawful to operate a motorboat during September, October and November."*
  — the only clean example found of a `season`-scoped restriction with no `hours` component; the
  season is stated as bare month names, not a date range, so `parse-dnr` needs a month-name → 
  `{start, end}` lookup rather than a date regex.
- No other pure-seasonal (no time-of-day) example was found in the sampled counties; most
  "season" flavor comes bundled into the specific-holidays pattern above (Middle Straits Lake,
  Indian River) rather than a clean month range.

### `no_vessels` — all vessels / boating prohibited (not just motorboats)

- Berrien, St. Joseph River at I&M Dam, WC-11-90-001 ("Boating prohibited"): *"...thence in a
  westerly direction to the nearest landfall, boating is prohibited."* (dam safety exclusion
  zone, described by a bearing-and-distance metes-and-bounds chain, not a section/T/R).
- Berrien, St. Joseph River, WC-11-91-002 ("Prohibition of vessels"): *"No vessel shall operate
  upon the following described waters of the St. Joseph river, within section 25, T7S, R18W,
  downstream from the dam belonging to the Indiana Michigan Power Company..."*
- Berrien, St. Joseph River at Niles, WC-11-92-001 ("Prohibition of vessels"): same template,
  different dam.

  All three known `no_vessels` examples are **dam-safety zones on rivers, not lakes** — worth
  flagging because these will not usefully match to a lake polygon in the `match` stage; they're
  candidates for `needs_review` / manual exclusion rather than automatic geocoding.

### Other templates the catalog above doesn't cover (all fall to `restriction_type: other` today)

- **Moorage/anchoring restriction**, Macomb, South Channel, R281.750.8: *"no vessel shall be
  moored, docked, or anchored or shall in any other manner obstruct or restrict the passage of
  other vessels, for a distance of 350 feet inland from Lake St. Clair."*
- **Airboats prohibited** (specific craft type, not "motorboat"), Oakland, Wolverine Lake,
  R281.763.31: *"it is unlawful to operate an airboat."*
- **Flotation devices / rafts**, Genesee, C.S. Mott Lake and Flint River, R281.725.6: *"(a)
  Operate an airboat. (b) Use a pneumatic or inflatable raft or flotation vessel or device in any
  area restricted to swimming, bathing or wading. (c) Use a single-celled pneumatic or inflatable
  raft, flotation vessel or device anywhere."*
- **Multi-part "prohibited conduct" omnibus rules** that bundle a statewide-style 100-ft buffer, a
  numeric speed cap, and a towing ban into one numbered rule with parenthetical sub-items, e.g.
  Oakland Stoney Creek Lake R281.763.2: *"(a) Rubber rafts and all floating devices, other than
  vessels, shall not be used except in swimming areas or special areas designated for their use.
  (b) No operator of any vessel shall operate such vessel within areas prohibited to boating. (c)
  No operator of any motorboat shall operate such motorboat at a rate of speed greater than 10
  statute miles per hour, except an authorized patrol boat or emergency rescue craft. (d) No
  operator of any motorboat shall have in tow... (See R281.750.1. for regulation covering the part
  of this lake lying in Macomb county.)"* — this single entry should decompose into at least three
  schema restriction rows (speed_limit 10 mph, no_towing, other/flotation), all sharing one
  rule_id.
- **Cross-county continuation notes**, appended parenthetically to a clause, pointing at the
  sibling rule for the rest of the same lake in a neighboring county, e.g. *"(See R281.744.2 for
  regulation covering the part of this lake lying in Hadley township, Lapeer county.)"* (Oakland,
  Davison Lake) and the mirror-image pair Oakland `R281.763.1` Kent Lake ↔ Livingston
  `R281.747.1` Kent Lake, each referencing the other. **Important for `match`**: a "Kent Lake" (or
  "Dunham Lake", "Big Silver Lake", "Hi-Land Lake") appears as a *separate row per county* for
  the same physical lake straddling a county line — the matcher needs to merge these onto one
  polygon rather than creating duplicate lakes, and the raw text of each half literally names the
  other county's rule number.
- **Rescinded rule left as a placeholder**, Clinton, Lake Ovid, R281.719.1: *"Rescinded August 17,
  1992. Publisher's Note: This watercraft order pertained to Electric Motors Only on Lake Ovid,
  Sleepy Hollow State Park, Victor township, ..."* — 14 such rescinded entries found; must not be
  emitted as active restrictions.
- **Statewide-rule echo** (restates the general 100-ft-buffer/no-wake-near-obstacles rule that
  already applies lakewide by statute, as a local rule): Oakland, Lake Orion, R281.763.9(1) and
  Lakeville Lake, R281.763.40(1): *"it is unlawful for the operator of a vessel to exceed a
  slow--no wake speed when within 100 feet of any shore, dock, raft, buoyed or occupied bathing
  area, or vessel moored or at anchor, except when water skiers are being picked up or dropped
  off..."* — same wording, word-for-word, on two different lakes in two different rule numbers.

### Rough corpus stats (for calibrating regex coverage expectations)

- 939 `<strong>`/`<b>` per-lake header entries counted across all 83 pages (includes rescinded
  entries and the 7 no-controls placeholders' single heading each).
- 672 occurrences of the `R281.x.x` numbering scheme; 299 of the `WC-xx-xx-xxx` scheme.
- 14 `Rescinded` entries.
- 0 PWC-specific entries.
- "water ski" (any form) appears 500 times — by far the dominant restriction family; motorboat
  speed/motor-type rules are the next largest bucket.

## 5. Michigan Administrative Code cross-check (R 281.7xx)

- **law.cornell.edu is fetchable with plain curl (browser UA not even required — tested and got
  HTTP 200) and resolves individual rules**, not just the Part-8 landing page. URL pattern:
  `https://www.law.cornell.edu/regulations/michigan/Mich-Admin-Code-R-<part>-<rule>[-<subrule>]`.
  Verified: `Mich-Admin-Code-R-281-763-3` returns a page titled *"Mich. Admin. Code R. 281.763.3 -
  Big and Little School Lot lakes and connecting channel; high-speed boating and water skiing
  prohibited"* — an exact match to the DNR page's text for that rule. `Mich-Admin-Code-R-281-763`
  (no subrule) resolves to a generic Part-8-level page, not the specific rule, so the subrule
  segment is required when the DNR page's rule number has one (`R281.763.3` → `-763-3`, not
  `-763`).
- **This only covers the legacy `R281.x.x` numbering scheme.** The newer `WC-xx-xx-xxx` director's
  orders are not part of the codified Administrative Code and were not found on Cornell (not
  tested exhaustively, but they are categorically a different instrument — orders under
  delegated authority, not rules adopted via APA rulemaking — so they should not be expected
  there).
- **ars.apps.lara.state.mi.us (the state's own Admin Code system) is not usable with plain curl.**
  The home page (`https://ars.apps.lara.state.mi.us/`) returns 200 but is a single-page app shell;
  the only useful static link found was `/AdminCode/AdminCode`, and a guessed direct-download URL
  returned an `/Error/Error` page. This site would need a headless browser (Playwright) or its
  underlying API reverse-engineered to be crawlable; **not recommended as the automated
  cross-check source** — use law.cornell.edu instead for the `R281.x` subset, and treat `WC-x`
  entries as uncheckable against a second source.
- Did not bulk-download Cornell; only fetched two rule pages (`R-259-401`, already referenced in
  design.md, and `R-281-763-3`) to confirm the pattern works, per the instruction not to mirror it.

## 6. Fixtures saved

`pipeline/tests/fixtures/dnr_pages/` (all lowercase, raw HTML, browser-UA fetch, 2026-09-15):

| File | Bytes | Why chosen |
|---|---|---|
| `_index.html` | 780,798 | The county accordion index (section 1). |
| `oakland.html` | 509,935 | Given fixture; largest page in the dataset (62 `R281.763.x` + dozens of `WC-63-xx-xxx` entries); only county sampled that mixes essentially every template in the catalog above, including the `Rescinded` and multi-clause-omnibus cases. |
| `cheboygan.html` | 465,466 | Given fixture; mid-size, clean example of the `<b>`-tagged header variant (vs. Oakland/Kent's `<strong>`), and has the day-beacon-range zone-description style and a Saturdays/Holidays/season towing rule. |
| `missaukee.html` | 460,059 | The "no controls" edge case — exercises the `-- No Special Local Watercraft Controls in This County --` placeholder and the `<p><b>` (not `<h2>`) heading variant. One of only 7 counties like this. |
| `livingston.html` | 474,418 | Large county (26 `R281.747.x` + several `WC-47-xx-xxx`); has the `WC - 47 - 01 - 001` spaced-dash rule-number format and two lakes (Kent Lake, Dunham Lake, Big Silver Lake, Hi-Land Lake) that cross-reference sibling rules in Oakland/Washtenaw for the same physical lake. |
| `kent.html` | 471,019 | Explicitly suggested in the task; medium-large, abbreviated-PLSS-only style (`T9N, R9W`, never the spelled-out "town/range" form), and an entry (Big Wabasis Lake) combining an hours-windowed ban with a flat mph cap in one rule. |

## 7. Gotchas, ranked

1. **Default curl UA is blocked (403); a browser UA works (200).** Every fetch in this recon used
   `User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like
   Gecko) Chrome/128.0.0.0 Safari/537.36`. The `fetch` stage must set a browser-like UA or every
   request will fail.
2. **The record-boundary tag is inconsistent (`<strong>` vs `<b>`) and the county-heading tag is
   inconsistent (`<h2>` vs `<p><b>`).** A brittle "look for `<h2>` then `<strong>`" parser will
   silently miss Cheboygan-style pages and crash or misparse Missaukee-style no-controls pages.
3. **Two independent, differently-formatted rule-number schemes coexist on the same page**
   (`R281.x.x`, spaced or not; `WC-xx-xx-xxx`, spaced/dashed/not-dashed) — and a `Rescinded` entry
   can drop the lake name from its header entirely, breaking the usual `"NAME - RULE - TITLE"`
   split.
4. **Two of 83 counties (Oakland, Emmet) break the `.../watercraft` URL-slug convention** — the
   fetch stage must read hrefs off the index accordion, never construct URLs from a county-name
   template, or it will 404 on exactly the two counties (Oakland) that matter most for this
   project's seed data (Lake Angelus, Big/Little School Lot Lake are both Oakland).
5. **7 of 83 counties intentionally have zero controls** and render a fixed placeholder sentence
   instead of an error — must be special-cased as "zero restrictions, success" rather than
   treated as a parse failure or retried.
6. **Multi-clause entries and cross-county lake splits mean "one DNR entry" ≠ "one restriction"
   and "one lake mention" ≠ "one physical lake".** Sub-clauses `(a)`/`(b)`/`(c)` under one header
   frequently mix restriction types (e.g. hours-ban + flat mph cap + zone no-wake in one Cass Lake
   entry); and lakes straddling a county line (Kent Lake, Dunham Lake, Big Silver Lake, Hi-Land
   Lake) get one full entry per county, each referencing the other's rule number in a parenthetical
   note — the `match` stage needs to de-duplicate these onto one polygon.
7. **No PWC-specific restriction text exists anywhere in the current 83-county corpus** —
   plan for `no_pwc` to be a schema slot that (as of this snapshot) never actually fires from the
   regex pass; don't spend parser effort hunting for a template that doesn't exist yet.
8. **Michigan Administrative Code cross-check only works for the legacy `R281.x` half of the
   data.** law.cornell.edu is curl-friendly and its URL is guessable
   (`Mich-Admin-Code-R-<part>-<rule>-<subrule>`), but it has nothing for `WC-xx-xx-xxx` orders, and
   the state's own ars.apps.lara.state.mi.us system is a JS single-page app that plain curl can't
   use.
9. **No rate limiting or UA-based blocking was encountered** fetching all 83 county pages plus the
   index (~90 requests, 0.25 s apart) — but `robots.txt` at michigan.gov doesn't disallow `/dnr/`,
   and pages send `cache-control: private, max-age=2780` (~46 min) with no visible `ETag`, so a
   weekly cron re-fetch is cheap and polite; there is no evidence a conditional-GET optimization is
   available (no ETag observed), so budget a full re-download each run.
10. **Every page is a large mostly-boilerplate wrapper** (~455-475 KB of Michigan.gov chrome/nav/
    footer/JS for a page whose actual content is 1-50 KB of text) — Oakland's `field-content` is
    ~50 KB inside a ~510 KB page. Parse only the `div.field-content` node; don't run regexes over
    the full document.

## References used

- Index: https://www.michigan.gov/dnr/managing-resources/laws/controls
- Oakland: https://www.michigan.gov/dnr/managing-resources/laws/controls/localcontrols/oakland/local-watercraft-controls
- Cornell cross-check pattern: https://www.law.cornell.edu/regulations/michigan/Mich-Admin-Code-R-281-763-3
- LARA Admin Code system (JS app, not curl-friendly): https://ars.apps.lara.state.mi.us/
