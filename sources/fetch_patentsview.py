"""
PatentsView bulk download helpers via the USPTO Open Data Portal (ODP).
No entry point; import and call what you need.

The legacy PatentsView API (search.patentsview.org) is shut down; the data now
lives in the ODP "Bulk Datasets" API at https://api.uspto.gov.

Requires an ODP API key (set USPTO_ODP_API_KEY, or pass api_key=...).

Example:
    from patentsview_download import list_products, download_patentsview

    products = list_products()                                  # inspect first
    failed = download_patentsview(out_dir="data/patentsview", workers=4)
    # or specific products:
    failed = download_patentsview(product_ids=["PRODUCT_ID"], out_dir="data/patentsview")

Files are saved to <out_dir>/<productIdentifier>/<fileName>. Existing files
are skipped, so download_patentsview() can be re-run to resume.

NOTE: untested against the live API. If list_products() returns nothing, check
the search parameter name in the ODP docs and adjust SEARCH_PARAM, or pass
product identifiers directly via product_ids.
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

BASE = "https://api.uspto.gov/api/v1/datasets/products"
SEARCH_PARAM = "q"  # query-string parameter for product search


def make_session(api_key=None):
    key = api_key or os.environ.get("USPTO_ODP_API_KEY")
    if not key:
        raise ValueError("No API key: pass api_key or set USPTO_ODP_API_KEY "
                         "(get one under 'My ODP' at data.uspto.gov).")
    s = requests.Session()
    s.headers.update({"x-api-key": key, "Accept": "application/json"})
    return s


def _find_key(obj, key):
    """Recursively collect every value stored under `key` (response nesting varies)."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            else:
                found.extend(_find_key(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_find_key(item, key))
    return found


def _get_products(data):
    bags = _find_key(data, "bulkDataProductBag")
    return [p for bag in bags for p in (bag if isinstance(bag, list) else [bag])]


def search_products(session, query="PatentsView"):
    r = session.get(f"{BASE}/search", params={SEARCH_PARAM: query}, timeout=60)
    r.raise_for_status()
    return _get_products(r.json())


def get_product(session, product_id):
    r = session.get(f"{BASE}/{product_id}", timeout=60)
    r.raise_for_status()
    products = _get_products(r.json())
    return products[0] if products else {}


def product_files(product):
    files = []
    for bag in _find_key(product, "fileDataBag"):
        files.extend(bag if isinstance(bag, list) else [bag])
    return files


def _file_name(f):
    return f.get("fileName") or Path(urlparse(f.get("fileDownloadURI", "")).path).name


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def list_products(query="PatentsView", api_key=None):
    """Log and return the products matching `query`."""
    session = make_session(api_key)
    products = search_products(session, query)
    for p in products:
        files = product_files(p)
        total = sum(float(f.get("fileSize") or 0) for f in files)
        log.info("%-14s %-70s %4d files  %s",
                 p.get("productIdentifier", "?"), p.get("productTitleText", "")[:70],
                 len(files), human_size(total) if total else "")
    return products


def collect_jobs(session, products, out_dir):
    out_dir = Path(out_dir)
    jobs = []
    for p in products:
        pid = p.get("productIdentifier")
        if not pid:
            continue
        # Search results may omit the file list; fetch the full product if so.
        files = product_files(p) or product_files(get_product(session, pid))
        for f in files:
            url, name = f.get("fileDownloadURI"), _file_name(f)
            if url and name:
                jobs.append((pid, url, out_dir / pid / name, f.get("fileSize")))
    return jobs


def download_file(session, url, dest, retries=3):
    """Stream to .part then rename, so an interrupted download never looks complete."""
    dest = Path(dest)
    if dest.exists():
        return "skipped"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            # Send the API key to api.uspto.gov only; if the server redirects to a
            # signed storage URL, follow it WITHOUT the key.
            r = session.get(url, stream=True, timeout=60, allow_redirects=False)
            hops = 0
            while r.is_redirect and hops < 5:
                r.close()
                r = requests.get(r.headers["Location"], stream=True, timeout=60,
                                 allow_redirects=False)
                hops += 1
            r.raise_for_status()
            with r, open(tmp, "wb") as out:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    out.write(chunk)
            tmp.rename(dest)
            return "downloaded"
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def download_patentsview(out_dir="patentsview_dump", query="PatentsView",
                         product_ids=None, workers=4, api_key=None, dry_run=False):
    """Download all files of the matching products.

    product_ids: optional list of product identifiers (skips the search).
    dry_run:     if True, only return the job list [(pid, url, dest, size), ...].
    Returns the list of failed file names (empty on success).
    """
    session = make_session(api_key)
    if product_ids:
        products = [get_product(session, pid) for pid in product_ids]
    else:
        products = search_products(session, query)
    if not products:
        raise LookupError("No products found. Try a different query or pass product_ids.")

    jobs = collect_jobs(session, products, out_dir)
    total = sum(float(j[3] or 0) for j in jobs)
    log.info("%d files%s", len(jobs), f", {human_size(total)} total" if total else "")
    if dry_run:
        return jobs

    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_file, session, url, dest): (pid, dest, size)
                   for pid, url, dest, size in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            pid, dest, size = futures[fut]
            try:
                log.info("[%d/%d] %s: %s/%s (%s)", i, len(jobs), fut.result(),
                         pid, dest.name, human_size(size))
            except Exception as e:
                failed.append(dest.name)
                log.error("[%d/%d] FAILED: %s/%s: %s", i, len(jobs), pid, dest.name, e)
    return failed