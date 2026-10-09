"""
CMS data helpers (no entry point; import and call what you need).

Sources covered
    data.cms.gov            Part B / Part D spending, Part D prescribers, Part D formulary, ...
    data.medicaid.gov       NADAC pricing, SDUD, ...                      (DKAN)
    openpaymentsdata.cms.gov  Open Payments (industry -> physician)       (DKAN)
    cms.gov / downloads.cms.gov  static file pages, e.g. ASP pricing files (HTML link scraping)

No API keys are needed for any of these.

Example:
    from cms_download import *

    # --- data.cms.gov ---------------------------------------------------
    for d in find_cms_datasets(["Part B Spending", "Part D Spending"]):
        print(d["title"])
    download_cms_datasets("data/cms", title_contains=["Part D Spending"], dry_run=True)
    for row in cms_iter_rows("<dataset-uuid>"):          # or stream rows through the API
        ...

    # --- data.medicaid.gov (NADAC) -------------------------------------
    download_dkan_datasets(MEDICAID_BASE, "data/medicaid", title_contains="NADAC")

    # --- Open Payments --------------------------------------------------
    ds = find_open_payments_datasets("General Payment", years=[2024])
    rows = open_payments_rows_for_npi(ds[0]["identifier"], npi=1234567890)

    # --- cms.gov file pages (ASP pricing files) -------------------------
    download_linked_files(CMS_PAGES["asp_pricing_files"], "data/asp", pattern=r"ASP Pricing File")

Downloads skip files that already exist, so every download_* function can be
re-run to resume. Files are written as .part and renamed when complete.
Each download_* function returns the list of failed URLs (or the job list when
dry_run=True).

NOTE: written against the public API docs; not run against the live services.
"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; cms-data-fetcher/1.0)"

CMS_DATA_JSON = "https://data.cms.gov/data.json"
CMS_API = "https://data.cms.gov/data-api/v1"
MEDICAID_BASE = "https://data.medicaid.gov"
OPEN_PAYMENTS_BASE = "https://openpaymentsdata.cms.gov"
CMS_PAGES = {
    "asp_pricing_files": "https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files",
}

# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
_local = threading.local()


def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=5, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST", "HEAD"]))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    return s


def _session():
    """One Session per thread (safe for the thread pools below)."""
    if not hasattr(_local, "session"):
        _local.session = make_session()
    return _local.session


def _terms(x):
    if not x:
        return []
    return [x] if isinstance(x, str) else list(x)


def _title_matches(title, terms):
    terms = _terms(terms)
    t = (title or "").lower()
    return not terms or any(term.lower() in t for term in terms)


def _safe(name, limit=120):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:limit] or "file"


def _url_filename(url):
    return unquote(Path(urlparse(url).path).name)


def remote_size(url):
    """Content-Length in bytes via HEAD, or None if the server doesn't say."""
    try:
        r = _session().head(url, allow_redirects=True, timeout=30)
        return int(r.headers["Content-Length"]) if "Content-Length" in r.headers else None
    except (requests.RequestException, ValueError):
        return None


def download_file(url, dest, retries=3):
    """Stream a file to disk via .part + rename, so a crash never leaves a
    corrupt file that looks complete."""
    dest = Path(dest)
    if dest.exists():
        return "skipped"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            with _session().get(url, stream=True, timeout=(30, 120)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            tmp.rename(dest)
            return "downloaded"
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def _run_downloads(jobs, workers=4):
    """jobs: list of (label, url, dest). Returns list of failed URLs."""
    log.info("%d files to process", len(jobs))
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_file, url, dest): (label, url, dest)
                   for label, url, dest in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            label, url, dest = futures[fut]
            try:
                log.info("[%d/%d] %s: %s -> %s", i, len(jobs), fut.result(), label, dest.name)
            except Exception as e:
                failed.append(url)
                log.error("[%d/%d] FAILED: %s (%s): %s", i, len(jobs), label, url, e)
    return failed


# ---------------------------------------------------------------------------
# data.cms.gov  (Data API + data.json catalog)
# ---------------------------------------------------------------------------
def cms_catalog():
    """Full data.json catalog: a list of dataset dicts (title, distribution, ...)."""
    r = _session().get(CMS_DATA_JSON, timeout=120)
    r.raise_for_status()
    return r.json().get("dataset", [])


def find_cms_datasets(title_contains=None, catalog=None):
    """Datasets whose title contains any of the given substrings (case-insensitive)."""
    catalog = catalog if catalog is not None else cms_catalog()
    return [d for d in catalog if _title_matches(d.get("title"), title_contains)]


def cms_distributions(dataset, fmt="CSV", latest_only=True):
    """Distributions of a catalog item in the given format ("CSV" or "API").
    With latest_only, keep those marked "latest" (falling back to the first)."""
    dists = [d for d in dataset.get("distribution", [])
             if str(d.get("format", "")).upper() == fmt.upper()]
    if latest_only:
        latest = [d for d in dists if str(d.get("description", "")).strip().lower() == "latest"]
        dists = latest or dists[:1]
    return dists


def cms_iter_rows(dataset, page_size=5000, params=None):
    """Yield every row of a data.cms.gov dataset through the Data API.

    dataset: dataset UUID, or a full .../data endpoint URL (e.g. an "API"
             distribution's accessURL, which pins a specific version).
    page_size: max 5000 per request. params: extra query parameters.
    Note: all values come back as strings.
    """
    url = dataset if dataset.startswith("http") else f"{CMS_API}/dataset/{dataset}/data"
    offset = 0
    while True:
        q = dict(params or {}, size=page_size, offset=offset)
        r = _session().get(url, params=q, timeout=120)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        yield from rows
        if len(rows) < page_size:
            return
        offset += page_size


def download_cms_datasets(out_dir="cms_dump", title_contains=None, latest_only=True,
                          workers=4, dry_run=False, catalog=None):
    """Download the CSV file(s) of matching data.cms.gov datasets.
    title_contains=None means EVERYTHING in the catalog (very large) - use dry_run first."""
    out_dir = Path(out_dir)
    jobs = []
    for item in find_cms_datasets(title_contains, catalog):
        for d in cms_distributions(item, "CSV", latest_only):
            url = d.get("downloadURL")
            if url:
                dest = out_dir / "data.cms.gov" / _safe(item.get("title", "")) / _safe(_url_filename(url))
                jobs.append((item.get("title", ""), url, dest))
    if dry_run:
        return jobs
    return _run_downloads(jobs, workers)


# ---------------------------------------------------------------------------
# DKAN portals: data.medicaid.gov and openpaymentsdata.cms.gov
# ---------------------------------------------------------------------------
def dkan_catalog(base_url, page_size=100):
    """All dataset metadata items from a DKAN metastore (handles servers that
    do or don't honour page/page-size)."""
    url = f"{base_url}/api/1/metastore/schemas/dataset/items"
    items, seen, page = [], set(), 1
    while True:
        r = _session().get(url, params={"page": page, "page-size": page_size}, timeout=120)
        r.raise_for_status()
        batch = r.json()
        new = [i for i in batch if i.get("identifier") not in seen]
        if not new:
            break
        items.extend(new)
        seen.update(i.get("identifier") for i in new)
        page += 1
    return items


def find_dkan_datasets(base_url, title_contains=None, years=None, catalog=None):
    """Filter a DKAN catalog by title substring(s) and/or year(s) appearing in the title."""
    catalog = catalog if catalog is not None else dkan_catalog(base_url)
    years = [str(y) for y in _terms(years)]
    return [d for d in catalog
            if _title_matches(d.get("title"), title_contains)
            and (not years or any(y in (d.get("title") or "") for y in years))]


def _dkan_download_urls(item):
    urls = []
    for d in item.get("distribution", []):
        if isinstance(d, dict):
            u = d.get("downloadURL") or (d.get("data") or {}).get("downloadURL")
            if u:
                urls.append(u)
    return urls


def download_dkan_datasets(base_url, out_dir, title_contains=None, years=None,
                           workers=4, dry_run=False, catalog=None):
    """Download the static bulk files of matching datasets on a DKAN portal.
    Open Payments files are multi-GB per reporting year; use dry_run first."""
    out_dir = Path(out_dir)
    host = urlparse(base_url).netloc
    jobs = []
    for item in find_dkan_datasets(base_url, title_contains, years, catalog):
        for url in _dkan_download_urls(item):
            dest = out_dir / host / _safe(item.get("title", "")) / _safe(_url_filename(url))
            jobs.append((item.get("title", ""), url, dest))
    if dry_run:
        return jobs
    return _run_downloads(jobs, workers)


def dkan_iter_rows(base_url, dataset_id, index=0, conditions=None, properties=None,
                   sorts=None, page_size=500):
    """Yield rows from a DKAN datastore query (server-side filtering, paged).

    conditions: [{"property": "col", "value": "x", "operator": "="}, ...]
    properties: list of columns to return.  sorts: [{"property": "col", "order": "asc"}]
    page_size: Open Payments defaults to 500 rows per request.
    """
    url = f"{base_url}/api/1/datastore/query/{dataset_id}/{index}"
    offset = 0
    while True:
        body = {"limit": page_size, "offset": offset, "count": True,
                "results": True, "schema": False, "keys": True}
        if conditions:
            body["conditions"] = conditions
        if properties:
            body["properties"] = properties
        if sorts:
            body["sorts"] = sorts
        r = _session().post(url, json=body, timeout=120)
        r.raise_for_status()
        data = r.json()
        rows = data.get("results", [])
        yield from rows
        offset += len(rows)
        total = data.get("count")
        if len(rows) < page_size or (total is not None and offset >= total):
            return


def find_medicaid_datasets(title_contains="NADAC", years=None, catalog=None):
    return find_dkan_datasets(MEDICAID_BASE, title_contains, years, catalog)


def find_open_payments_datasets(title_contains=None, years=None, catalog=None):
    return find_dkan_datasets(OPEN_PAYMENTS_BASE, title_contains, years, catalog)


def open_payments_rows_for_npi(dataset_id, npi, **kwargs):
    """All rows in one Open Payments dataset for a covered recipient's NPI."""
    cond = [{"property": "covered_recipient_npi", "value": str(npi), "operator": "="}]
    return dkan_iter_rows(OPEN_PAYMENTS_BASE, dataset_id, conditions=cond, **kwargs)


# ---------------------------------------------------------------------------
# cms.gov / downloads.cms.gov file pages (e.g. ASP pricing files)
# ---------------------------------------------------------------------------
class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []          # (anchor text, href)
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((" ".join("".join(self._text).split()), self._href))
            self._href = None


def _is_license_gated(url):
    u = url.lower()
    return "/license/ama" in u or "/apps/ama/license" in u


def scrape_file_links(page_url, extensions=(".zip", ".csv", ".xlsx", ".xls", ".txt", ".pdf"),
                      pattern=None):
    """Return [{"text", "url", "gated"}] for file links on a page.
    pattern: regex matched (case-insensitive) against the link text or URL.
    Links behind CMS's AMA licence click-through are flagged gated=True."""
    r = _session().get(page_url, timeout=60)
    r.raise_for_status()
    parser = _LinkParser()
    parser.feed(r.text)
    rx = re.compile(pattern, re.I) if pattern else None
    out, seen = [], set()
    for text, href in parser.links:
        url = urljoin(page_url, href)
        gated = _is_license_gated(url)
        if not gated and Path(urlparse(url).path).suffix.lower() not in extensions:
            continue
        if rx and not (rx.search(text) or rx.search(url)):
            continue
        if url not in seen:
            seen.add(url)
            out.append({"text": text, "url": url, "gated": gated})
    return out


def download_linked_files(page_url, out_dir, pattern=None,
                          extensions=(".zip", ".csv", ".xlsx", ".xls", ".txt", ".pdf"),
                          workers=4, dry_run=False):
    """Download the files linked from a cms.gov page.

    Links that go through CMS's AMA/CPT licence click-through (older ASP files,
    pre-2021) are NOT downloaded: accept the licence on the site and fetch those
    manually. They are logged and, with dry_run=True, included in the result.
    """
    out_dir = Path(out_dir)
    links = scrape_file_links(page_url, extensions, pattern)
    gated = [l for l in links if l["gated"]]
    if gated:
        log.warning("%d link(s) require accepting the AMA licence and were skipped", len(gated))
    jobs = [(l["text"], l["url"], out_dir / _safe(_url_filename(l["url"])))
            for l in links if not l["gated"]]
    if dry_run:
        return jobs + [(l["text"] + " [licence-gated]", l["url"], None) for l in gated]
    return _run_downloads(jobs, workers)