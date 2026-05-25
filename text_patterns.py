#!/usr/bin/env python3
"""
text_patterns.py -- ZO-SENTINEL extended text pattern library.
Extends known_threats.py with injection, credential harvesting,
and obfuscation detection patterns for tool descriptions.
"""
import re
from typing import Dict, List

INJECTION_PATTERNS = [
    r"ignore previous instructions",
    r"ignore all prior",
    r"disregard (?:any )?previous",
    r"disregard system (?:prompt|instructions)",
    r"system prompt:",
    r"override (?:your |the )?(?:system|builtin)",
    r"pretend you are",
    r"act as if you were",
    r"you are now",
    r"new system prompt",
    r"ignore the above",
    r"forget (?:all )?previous",
    r"disregard (?:all |any )?instructions",
    r"do not follow (?:your |the )?(?:rules|guidelines)",
    r"instead behave as",
    r"bypass (?:safety|security|filter)",
    r"jailbreak",
    r"\\x00",  # null byte injection
]

CREDENTIAL_HARVESTING_PATTERNS = [
    r"send (?:my |your |the )?(?:credentials|api keys?|tokens?|passwords?)",
    r"exfiltrate",
    r"upload (?:your |my )?private key",
    r"env(?:ironment)? var(?:iable)? dump",
    r"steal (?:credentials|secrets|keys)",
    r"capture (?:credentials|api keys?|tokens?)",
    r"extract (?:credentials|secrets)",
    r"log (?:all )?(?:credentials|api keys?|passwords?)",
    r"collect (?:credentials|secrets)",
    r"harvest (?:credentials|secrets|keys)",
    r"transmit (?:credentials|api keys?|tokens?)",
    r"post (?:my |your )?(?:credentials|api keys?)",
]

OBFUSCATION_PATTERNS = [
    r"[A-Za-z0-9+/]{50,}={0,2}",  # base64 strings
    r"\\x[0-9a-fA-F]{2}",         # hex escapes
    r"\\u[0-9a-fA-F]{4}",         # unicode escapes
    r"\u200b|\u200c|\u200d",      # zero-width characters
    r"\u202e|\u202d",             # unicode directional override
    r"\x90\xd0",                  # x86 NOP sled pattern
    r"<script",                   # potential XSS
    r"javascript:",               # potential XSS
    r"data:text/html",            # data URL injection
]

PATTERN_SEVERITY = {
    "injection": 30,
    "credential": 40,
    "obfuscation": 20,
}

def scan_description(text: str) -> Dict:
    """
    Scan tool description text for suspicious patterns.
    
    Args:
        text: The tool description to scan
        
    Returns:
        dict with keys:
            - injections: list of matched injection patterns
            - credentials: list of matched credential patterns
            - obfuscation: list of matched obfuscation patterns
            - score_penalty: int total penalty to subtract from trust score
    """
    result = {
        "injections": [],
        "credentials": [],
        "obfuscation": [],
        "score_penalty": 0,
    }
    
    if not text:
        return result
    
    # Scan for injection patterns
    for pattern in INJECTION_PATTERNS:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                result["injections"].append(pattern)
        except re.error:
            pass
    
    # Scan for credential harvesting patterns
    for pattern in CREDENTIAL_HARVESTING_PATTERNS:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                result["credentials"].append(pattern)
        except re.error:
            pass
    
    # Scan for obfuscation patterns
    for pattern in OBFUSCATION_PATTERNS:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                result["obfuscation"].append(pattern)
        except re.error:
            pass
    
    # Calculate score penalty
    result["score_penalty"] = (
        len(result["injections"]) * PATTERN_SEVERITY["injection"] +
        len(result["credentials"]) * PATTERN_SEVERITY["credential"] +
        len(result["obfuscation"]) * PATTERN_SEVERITY["obfuscation"]
    )
    
    return result