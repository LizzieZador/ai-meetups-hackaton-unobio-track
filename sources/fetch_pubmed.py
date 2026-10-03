"""PubMed source module — E-utilities API (esearch/efetch), free, no API key.

Owner: Науковий аналітик.
"""
import datetime
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from .cache import cached

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def fetch_pubmed(query: str, max_results: int = 10) -> Optional[list[dict]]:
    """Search PubMed and return [{source, url, date_accessed, raw_snippet}, ...].

    Returns None if the API is unreachable — caller must not crash the pipeline.
    """
    return cached("pubmed", query, lambda: _fetch(query, max_results))


def _fetch(query: str, max_results: int) -> Optional[list[dict]]:
    try:
        search_resp = requests.get(
            ESEARCH_URL,
            params={"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"},
            timeout=15,
        )
        search_resp.raise_for_status()
        ids = search_resp.json()["esearchresult"]["idlist"]
        if not ids:
            return []

        fetch_resp = requests.get(
            EFETCH_URL,
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=15,
        )
        fetch_resp.raise_for_status()
        root = ET.fromstring(fetch_resp.content)

        today = datetime.date.today().isoformat()
        results = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", default="")
            title = article.findtext(".//ArticleTitle", default="")
            abstract = " ".join(t.text or "" for t in article.findall(".//AbstractText"))
            results.append({
                "source": f"PubMed PMID:{pmid}",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "date_accessed": today,
                "raw_snippet": f"{title} — {abstract}"[:2000],
            })
        return results
    except (requests.RequestException, ET.ParseError, KeyError):
        return None
