"""
report_formatter.py - Markdown report formatter utility for ZO-SENTINEL.

Functions:
- header(text, level=1) -> str
- table(headers, rows) -> str (markdown table)
- badge(text, style) -> str (CRITICAL/HIGH/MEDIUM/LOW emoji badges)
- progress_bar(value, max_val, width=20) -> str (ASCII ████░░░ bar)
- section(title, content) -> str
- verdict_badge(verdict) -> str

No daemon. Used by all report-generating daemons.
"""

from typing import List, Dict, Any


# Verdict to emoji+text mapping
VERDICT_BADGES = {
    "TRUSTED_GENERAL": "✅ TRUSTED_GENERAL",
    "TRUSTED_RESEARCH": "🔵 TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED": "🟡 ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED": "🟠 CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED": "🔴 HIGH_RISK_ISOLATED",
    "KNOWN_THREAT": "💀 KNOWN_THREAT",
    "INSUFFICIENT": "⚪ INSUFFICIENT",
}

# Style to emoji prefix mapping
STYLE_BADGES = {
    "CRITICAL": "🔴 CRITICAL",
    "HIGH": "🟠 HIGH",
    "MEDIUM": "🟡 MEDIUM",
    "LOW": "🟢 LOW",
    "INFO": "🔵 INFO",
}


def header(text: str, level: int = 1) -> str:
    """
    Generate a markdown header.
    
    Args:
        text: The header text
        level: Header level (1-6)
    
    Returns:
        Markdown formatted header string
    """
    if level < 1:
        level = 1
    if level > 6:
        level = 6
    
    prefix = "#" * level
    return f"{prefix} {text}\n"


def table(headers: List[str], rows: List[List[Any]]) -> str:
    """
    Generate a markdown table.
    
    Args:
        headers: List of column header strings
        rows: List of row data (list of values per row)
    
    Returns:
        Markdown formatted table string
    """
    if not headers:
        return ""
    
    # Build header row
    header_row = "| " + " | ".join(str(h) for h in headers) + " |"
    
    # Build separator row
    separator_row = "| " + " | ".join("---" for _ in headers) + " |"
    
    # Build data rows
    data_rows = []
    for row in rows:
        row_str = "| " + " | ".join(str(cell) for cell in row) + " |"
        data_rows.append(row_str)
    
    # Combine all parts
    parts = [header_row, separator_row] + data_rows
    return "\n".join(parts) + "\n"


def badge(text: str, style: str) -> str:
    """
    Generate an emoji badge for text based on style.
    
    Args:
        text: The badge text
        style: One of CRITICAL, HIGH, MEDIUM, LOW, INFO
    
    Returns:
        Emoji-prefixed badge string
    """
    style_upper = style.upper()
    if style_upper in STYLE_BADGES:
        return f"{STYLE_BADGES[style_upper]}: {text}"
    # Default fallback
    return f"🏷️ {text}"


def progress_bar(value: float, max_val: float, width: int = 20) -> str:
    """
    Generate an ASCII progress bar.
    
    Args:
        value: Current value
        max_val: Maximum value
        width: Bar width in characters (default 20)
    
    Returns:
        ASCII progress bar string (e.g., "████░░░░░░░░░░░░░")
    """
    if max_val <= 0:
        return "░" * width
    
    # Calculate fill ratio (clamped to 0-1)
    ratio = value / max_val
    if ratio < 0:
        ratio = 0
    if ratio > 1:
        ratio = 1
    
    # Calculate filled and empty portions
    filled = int(ratio * width)
    empty = width - filled
    
    # Build bar with block character
    bar = "█" * filled + "░" * empty
    
    # Return with percentage
    pct = int(ratio * 100)
    return f"[{bar}] {pct}%"


def section(title: str, content: str) -> str:
    """
    Generate a markdown section with title and content.
    
    Args:
        title: Section title
        content: Section content (can include newlines)
    
    Returns:
        Formatted section string
    """
    separator = "─" * len(title)
    result = f"## {title}\n{separator}\n{content}\n"
    return result


def verdict_badge(verdict: str) -> str:
    """
    Generate an emoji badge for a verdict.
    
    Args:
        verdict: Verdict string (e.g., "TRUSTED_GENERAL", "HIGH_RISK_ISOLATED")
    
    Returns:
        Emoji-prefixed verdict string
    """
    verdict_upper = verdict.upper()
    if verdict_upper in VERDICT_BADGES:
        return VERDICT_BADGES[verdict_upper]
    # Unknown verdict fallback
    return f"❓ {verdict}"


def risk_badge(score: float) -> str:
    """
    Generate a risk badge based on a numeric score.
    
    Args:
        score: Risk score (0-100)
    
    Returns:
        Emoji-prefixed risk level string
    """
    if score >= 80:
        return badge(f"Risk Score: {score:.1f}", "CRITICAL")
    elif score >= 60:
        return badge(f"Risk Score: {score:.1f}", "HIGH")
    elif score >= 40:
        return badge(f"Risk Score: {score:.1f}", "MEDIUM")
    else:
        return badge(f"Risk Score: {score:.1f}", "LOW")


def format_server_row(server: Dict[str, Any]) -> List[str]:
    """
    Format a server record as a table row.
    
    Args:
        server: Server data dictionary
    
    Returns:
        List of formatted cell values
    """
    return [
        server.get("server_id", "N/A")[:16],
        server.get("name", "N/A")[:24],
        server.get("verdict", "UNKNOWN"),
        server.get("trust_score", 0),
    ]


def format_verdict_change(old_verdict: str, new_verdict: str) -> str:
    """
    Format a verdict change notification.
    
    Args:
        old_verdict: Previous verdict
        new_verdict: New verdict
    
    Returns:
        Formatted change notification string
    """
    old_badge = verdict_badge(old_verdict)
    new_badge = verdict_badge(new_verdict)
    return f"Verdict Change: {old_badge} → {new_badge}"


def format_list(items: List[str], ordered: bool = False) -> str:
    """
    Format a list as markdown.
    
    Args:
        items: List of strings to format
        ordered: If True, use numbered list; else bullet list
    
    Returns:
        Formatted markdown list string
    """
    if not items:
        return ""
    
    lines = []
    for i, item in enumerate(items):
        if ordered:
            lines.append(f"{i + 1}. {item}")
        else:
            lines.append(f"- {item}")
    
    return "\n".join(lines) + "\n"