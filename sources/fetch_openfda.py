"""
openFDA bulk download helpers (no entry point; import and call what you need).

Example:
    from openfda_download import report_sizes, download_all, iter_partitions

    report_sizes(only={"drug/label"})
    failed = download_all(out_dir="data/openfda", only={"drug/label", "drug/ndc"}, workers=8)

    for zip_path, records in iter_partitions("data/openfda"):
        ...

Files are saved under <out_dir>/ mirroring the server's folder structure.
Existing files are skipped, so download_all() can be re-run to resume.
"""
import json
import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

MANIFEST_URL = "https://api.fda.gov/download.json"


def get_manifest():
    r = requests.get(MANIFEST_URL, timeout=60)
    r.raise_for_status()
    return r.json()["results"]


def report_sizes(manifest=None, only=None):
    """Log record counts and compressed size per endpoint.
    Returns (rows, total_gb), where rows is a list of dicts."""
    manifest = manifest or get_manifest()
    rows, total_mb = [], 0.0
    for category, endpoints in manifest.items():
        for endpoint, info in endpoints.items():
            key = f"{category}/{endpoint}"
            if only and key not in only:
                continue
            size_mb = sum(float(p["size_mb"]) for p in info["partitions"])
            total_mb += size_mb
            rows.append({"endpoint": key,
                         "records": info["total_records"],
                         "size_gb": size_mb / 1024})
            log.info("%-28s %12s records  %8.2f GB",
                     key, f"{info['total_records']:,}", size_mb / 1024)
    log.info("Total (compressed): %.1f GB", total_mb / 1024)
    return rows, total_mb / 1024


def build_jobs(manifest, out_dir, only=None):
    out_dir = Path(out_dir)
    jobs = []
    for category, endpoints in manifest.items():
        for endpoint, info in endpoints.items():
            key = f"{category}/{endpoint}"
            if only and key not in only:
                continue
            for p in info["partitions"]:
                url = p["file"]
                dest = out_dir / urlparse(url).path.lstrip("/")
                jobs.append((key, url, dest, p["size_mb"]))
    return jobs


def download_file(url, dest, retries=3):
    """Stream a file to disk. Writes to .part first so a crash never leaves
    a corrupt file that looks complete."""
    dest = Path(dest)
    if dest.exists():
        return "skipped"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
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


def download_all(out_dir="openfda_dump", only=None, workers=4, manifest=None):
    """Download every partition (or only the given endpoints, e.g. {"drug/label"}).
    Returns a list of URLs that failed; re-call to retry them."""
    manifest = manifest or get_manifest()
    jobs = build_jobs(manifest, out_dir, only)
    log.info("%d files to process", len(jobs))
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_file, url, dest): (key, url, dest, size)
                   for key, url, dest, size in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            key, url, dest, size = futures[fut]
            try:
                log.info("[%d/%d] %s: %s %s (%s MB)",
                         i, len(jobs), fut.result(), key, dest.name, size)
            except Exception as e:
                failed.append(url)
                log.error("[%d/%d] FAILED: %s %s: %s", i, len(jobs), key, dest.name, e)
    return failed


def read_partition(zip_path):
    """Load one partition's records into memory (can be several GB for the
    biggest event files; process one partition at a time)."""
    with zipfile.ZipFile(zip_path) as z:
        with z.open(z.namelist()[0]) as f:
            return json.load(f)["results"]


def iter_partitions(root="openfda_dump"):
    """Yield (zip_path, records) for every downloaded partition under root."""
    for zp in sorted(Path(root).rglob("*.json.zip")):
        yield zp, read_partition(zp)