"""PubMed source module — NCBI E-utilities API (esearch + efetch).

Fetches biomedical literature abstracts with strict date filtering,
rate limiting, retry logic, and deduplication.

Owner: Scientific Analyst.
"""
import datetime
import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from .cache import CACHE_DIR, _slug

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Rate limiting state (NCBI allows max 3 req/sec without key, 10 req/sec with key)
_last_request_time = 0.0


def _throttle() -> None:
    """Enforce NCBI rate limits: <= 3 req/sec without key, <= 10 req/sec with key."""
    global _last_request_time
    api_key = os.environ.get("NCBI_API_KEY", "").strip()
    min_interval = 0.11 if api_key else 0.35
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()


def _request_with_retry(url: str, params: dict, timeout: int = 15, max_retries: int = 3) -> Optional[requests.Response]:
    """Execute GET request with exponential backoff on 429 and 5xx errors."""
    for attempt in range(max_retries):
        _throttle()
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.0 + attempt * 2.0)
                continue
            return None
        except requests.RequestException:
            if attempt < max_retries - 1:
                time.sleep(1.0 + attempt * 1.5)
                continue
            return None
    return None


def fetch_pubmed(
    query: str, max_results: int = 8, cutoff_date: Optional[str] = None
) -> Optional[list[dict]]:
    """Search PubMed and return list of evidence records or None on failure.

    Args:
        query: Search term / query string.
        max_results: Max articles to fetch (default 8).
        cutoff_date: Optional YYYY-MM-DD date threshold. Articles published
                     after this date are filtered out.

    Returns:
        List of dicts: {source, url, date_accessed, raw_snippet, pmid, pub_date, publication_types}
        or None if API fails. None is never cached.
    """
    if not query or not query.strip():
        return []

    cache_key = f"{query.strip()}__{max_results}__{cutoff_date or 'none'}__peer-reviewed"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
    slug_part = _slug(query)[:50]
    cache_path = CACHE_DIR / f"pubmed_{slug_part}_{digest}.json"

    if cache_path.exists():
        try:
            return [r for r in json.loads(cache_path.read_text(encoding="utf-8")) if _trustworthy(r)]
        except (OSError, ValueError):
            pass

    records = _fetch(query.strip(), max_results, cutoff_date)
    if records is not None:
        records = [r for r in records if _trustworthy(r)]
    if records is not None:
        try:
            cache_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    return records


# Not peer-reviewed findings: retracted papers, the retraction notices
# themselves, expressions of concern, errata and unreviewed preprints.
_UNRELIABLE_TYPES = {
    "Retracted Publication", "Retraction of Publication", "Expression of Concern",
    "Published Erratum", "Preprint",
}
_EXCLUDE_TERM = " NOT (" + " OR ".join(f'"{t.lower()}"[pt]' for t in sorted(_UNRELIABLE_TYPES)) + ")"


def _trustworthy(record: dict) -> bool:
    return not (set(record.get("publication_types") or []) & _UNRELIABLE_TYPES)


def _fetch(query: str, max_results: int, cutoff_date: Optional[str]) -> Optional[list[dict]]:
    """Live NCBI E-utilities fetch implementation."""
    api_key = os.environ.get("NCBI_API_KEY", "").strip()
    email = os.environ.get("NCBI_EMAIL", "agent@biotech.internal")
    tool = "biotech_underwriting"

    search_params = {
        "db": "pubmed",
        "term": f"({query}){_EXCLUDE_TERM}",
        "retmax": max_results,
        "retmode": "json",
        "sort": "pub_date",
        "tool": tool,
        "email": email,
    }
    if api_key:
        search_params["api_key"] = api_key

    # Date filter in esearch if cutoff_date is specified
    if cutoff_date:
        search_params["datetype"] = "pdat"
        search_params["mindate"] = "1800/01/01"
        search_params["maxdate"] = cutoff_date.replace("-", "/")

    search_resp = _request_with_retry(ESEARCH_URL, search_params)
    if search_resp is None:
        return None

    try:
        data = search_resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
    except (ValueError, KeyError):
        return None

    if not ids:
        return []

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
        "tool": tool,
        "email": email,
    }
    if api_key:
        fetch_params["api_key"] = api_key

    fetch_resp = _request_with_retry(EFETCH_URL, fetch_params)
    if fetch_resp is None:
        return None

    try:
        root = ET.fromstring(fetch_resp.content)
    except ET.ParseError:
        return None

    today = datetime.date.today().isoformat()
    results = []
    seen_pmids = set()

    for article in root.findall(".//PubmedArticle"):
        pmid = (article.findtext(".//PMID") or "").strip()
        if not pmid or pmid in seen_pmids:
            continue

        title = re.sub(r"\s+", " ", (article.findtext(".//ArticleTitle") or "").strip())
        pub_types = [
            t.text.strip()
            for t in article.findall(".//PublicationTypeList/PublicationType")
            if t.text and t.text.strip()
        ]

        # Extract publication date
        pub_date_str, pub_year = _extract_pub_date(article)

        # In-code date check against cutoff_date (strict verification)
        if cutoff_date and pub_date_str:
            if _is_after_cutoff(pub_date_str, cutoff_date):
                continue

        # Extract abstract text
        abstract_parts = []
        for elem in article.findall(".//Abstract/AbstractText"):
            label = elem.get("Label")
            text = "".join(elem.itertext()).strip()
            if text:
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)

        abstract = re.sub(r"\s+", " ", " ".join(abstract_parts)).strip()

        # Format raw_snippet concisely (Title [Types] (Year): Abstract, ~1200 chars)
        type_str = f" [{', '.join(pub_types[:3])}]" if pub_types else ""
        year_str = f" ({pub_year})" if pub_year else ""
        header = f"{title}{type_str}{year_str}"
        if abstract:
            full_text = f"{header}: {abstract}"
        else:
            full_text = header

        raw_snippet = full_text[:1200]

        results.append({
            "source": f"PubMed PMID:{pmid}",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "date_accessed": today,
            "raw_snippet": raw_snippet,
            "pmid": pmid,
            "pub_date": pub_date_str,
            "publication_types": pub_types,
        })
        seen_pmids.add(pmid)

    return results


def _extract_pub_date(article: ET.Element) -> tuple[str, str]:
    """Extract normalized publication date string (YYYY-MM-DD or YYYY-MM or YYYY) and year."""
    # 1. Try ArticleDate (electronic date with year, month, day)
    art_date = article.find(".//ArticleDate")
    if art_date is not None:
        y = art_date.findtext("Year") or ""
        m = art_date.findtext("Month") or ""
        d = art_date.findtext("Day") or ""
        if y:
            m_norm = m.zfill(2) if m.isdigit() else "01"
            d_norm = d.zfill(2) if d.isdigit() else "01"
            return f"{y}-{m_norm}-{d_norm}", y

    # 2. Try JournalIssue PubDate
    y = article.findtext(".//JournalIssue/PubDate/Year") or ""
    m = article.findtext(".//JournalIssue/PubDate/Month") or ""
    d = article.findtext(".//JournalIssue/PubDate/Day") or ""
    if y:
        m_norm = _month_to_num(m)
        d_norm = d.zfill(2) if d.isdigit() else "01"
        return f"{y}-{m_norm}-{d_norm}", y

    # 3. Try MedlineDate (e.g., '2021 Nov-Dec')
    medline_date = article.findtext(".//JournalIssue/PubDate/MedlineDate") or ""
    year_match = re.search(r"\b(19\d\d|20\d\d)\b", medline_date)
    if year_match:
        y = year_match.group(1)
        return y, y

    return "", ""


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _month_to_num(month_str: str) -> str:
    """Convert month name or string to 2-digit number."""
    if not month_str:
        return "01"
    if month_str.isdigit():
        return month_str.zfill(2)
    clean = month_str.strip().lower()[:3]
    return _MONTH_MAP.get(clean, "01")


def _is_after_cutoff(pub_date_str: str, cutoff_date: str) -> bool:
    """Compare publication date against cutoff_date (YYYY-MM-DD)."""
    # If pub_date is just YYYY: compare YYYY with cutoff_date[:4]
    if len(pub_date_str) == 4 and pub_date_str.isdigit():
        return pub_date_str > cutoff_date[:4]
    # Otherwise compare prefix up to length of pub_date_str
    return pub_date_str[:10] > cutoff_date[:10]
