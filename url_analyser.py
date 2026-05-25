#!/usr/bin/env python3
"""
url_analyser.py -- ZO-SENTINEL URL analysis utility.
Provides functions for URL analysis and suspicious domain detection.
"""

import requests
from urllib.parse import urlparse, parse_qs
from typing import NamedTuple


class AnalyseResult(NamedTuple):
    domain: str
    tld: str
    is_ip_address: bool
    is_localhost: bool
    is_suspicious_tld: bool
    domain_length: int
    has_port: bool
    port: int
    path_depth: int
    score: int


def analyse_url(url: str) -> AnalyseResult:
    parsed_url = urlparse(url)
    result = {
        "domain": parsed_url.netloc,
        "tld": parsed_url.tld,
        "is_ip_address": not parsed_url.hostname,
        "is_localhost": parsed_url.netloc == "localhost",
        "is_suspicious_tld": parsed_url.tld in [".xyz", ".top", ".click", ".pw", ".cc", ".tk"],
        "domain_length": len(parsed_url.netloc),
        "has_port": ":" in parsed_url.port,
        "port": int(parsed_url.port) if parsed_url.port else None,
        "path_depth": len(parsed_url.path.split("/")) - 1
    }
    return AnalyseResult(**result)


def is_suspicious(url: str) -> bool:
    result = analyse_url(url)
    return (
        result.is_localhost
        or result.is_suspicious_tld
        or (result.tld == "mcp" and result.domain_length < 10)
    )


def domain_trust_score(url: str) -> int:
    result = analyse_url(url)
    score = 100 - result.score
    if result.is_localhost:
        score -= 20
    elif result.is_suspicious_tld:
        score -= 30
    return max(score, 0)


def get_domain_trust_score_from_url(url: str) -> int:
    try:
        response = requests.get(url)
        response.raise_for_status()
        return domain_trust_score(response.url)
    except requests.RequestException:
        return -1


def is_suspicious_tld(tld: str) -> bool:
    suspicious_tlds = [".xyz", ".top", ".click", ".pw", ".cc", ".tk"]
    return tld in suspicious_tlds