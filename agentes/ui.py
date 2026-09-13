"""Terminal UI design system: elegant, sober, organized and accessible."""
import os
import re
import sys
import unicodedata

# Check color support (respects NO_COLOR and terminal capabilities)
def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return False
    return True

COLOR = _supports_color()


def clean(text):
    """Strip ANSI escape sequences."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text))
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)


def str_width(text):
    """Compute exact terminal display width accounting for wide chars and emojis."""
    raw = clean(text)
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in raw)


def style(text, *codes):
    if not COLOR or not codes:
        return str(text)
    prefix = "".join(f"\033[{c}m" for c in codes)
    return f"{prefix}{text}\033[0m"


def bold(text):
    return style(text, "1")


def dim(text):
    return style(text, "90")


def italic(text):
    return style(text, "3")


def cyan(text):
    return style(text, "36")


def bold_cyan(text):
    return style(text, "1", "36")


def green(text):
    return style(text, "32")


def bold_green(text):
    return style(text, "1", "32")


def yellow(text):
    return style(text, "33")


def bold_yellow(text):
    return style(text, "1", "33")


def red(text):
    return style(text, "31")


def bold_red(text):
    return style(text, "1", "31")


def magenta(text):
    return style(text, "35")


def blue(text):
    return style(text, "34")


def badge(label, kind="info"):
    """Render a compact sober status badge."""
    if kind in ("success", "ok", "succeeded"):
        return style(f"[{label}]", "1", "32")
    if kind in ("warning", "paused", "pause"):
        return style(f"[{label}]", "1", "33")
    if kind in ("error", "failed", "fail"):
        return style(f"[{label}]", "1", "31")
    if kind in ("running", "active"):
        return style(f"[{label}]", "1", "36")
    if kind in ("accent", "purple"):
        return style(f"[{label}]", "1", "35")
    return style(f"[{label}]", "90")


def box_header(title, subtitle=None, width=68):
    """Render an elegant rounded header card."""
    top = dim("╭" + "─" * (width - 2) + "╮")
    bot = dim("╰" + "─" * (width - 2) + "╯")
    
    t_w = str_width(title)
    pad_t = max(0, width - 4 - t_w)
    title_line = f"{dim('│')}  {bold_cyan(title)}{' ' * pad_t}{dim('│')}"
    
    lines = ["", top, title_line]
    if subtitle:
        s_w = str_width(subtitle)
        pad_s = max(0, width - 4 - s_w)
        sub_line = f"{dim('│')}  {dim(subtitle)}{' ' * pad_s}{dim('│')}"
        lines.append(sub_line)
    lines.append(bot)
    return "\n".join(lines)


def section_divider(title="", width=68):
    """Render a clean horizontal section divider with title."""
    t_str = f" {title} " if title else ""
    t_w = str_width(t_str)
    rem = max(0, width - 4 - t_w)
    return dim("───" + t_str + "─" * rem)


def menu_card(items, title=None, width=68):
    """
    Render a clean menu with keys, labels and optional descriptions.
    items: list of (key, label, description) or (key, label)
    """
    lines = []
    if title:
        lines.append(dim("╭─ ") + bold(title) + " " + dim("─" * max(0, width - 5 - str_width(title)) + "╮"))
    
    max_label_w = 0
    for item in items:
        label = item[1]
        max_label_w = max(max_label_w, str_width(label))
    max_label_w = min(max_label_w, 36)

    for item in items:
        key = str(item[0])
        label = item[1]
        desc = item[2] if len(item) > 2 and item[2] else ""
        
        lbl_w = str_width(label)
        pad_lbl = " " * max(1, max_label_w - lbl_w + 2)
        desc_part = dim(f"{desc}") if desc else ""
        
        key_styled = bold_cyan(f"{key:>2}")
        sep = dim("│")
        lines.append(f"  {key_styled} {sep} {label}{pad_lbl}{desc_part}")
    
    if title:
        lines.append(dim("╰" + "─" * (width - 2) + "╯"))
    return "\n".join(lines)


def table(headers, rows, alignments=None, width=68):
    """
    Render a clean, sober table with aligned columns.
    headers: list of column headers
    rows: list of lists of column values
    alignments: list of 'left' or 'right'
    """
    num_cols = len(headers)
    col_widths = [str_width(h) for h in headers]
    
    for row in rows:
        for idx, val in enumerate(row[:num_cols]):
            col_widths[idx] = max(col_widths[idx], str_width(str(val)))
    
    total_w = sum(col_widths) + (num_cols - 1) * 3
    if total_w > width:
        excess = total_w - width
        col_widths[-1] = max(10, col_widths[-1] - excess)
    
    # Header line
    h_parts = []
    for idx, h in enumerate(headers):
        w = col_widths[idx]
        pad = " " * max(0, w - str_width(h))
        h_parts.append(bold(h) + pad)
    header_line = "  " + "   ".join(h_parts)
    
    # Separator
    sep_line = "  " + dim("─" * min(width - 4, sum(col_widths) + (num_cols - 1) * 3))
    
    out_lines = [header_line, sep_line]
    for row in rows:
        r_parts = []
        for idx in range(num_cols):
            val = str(row[idx]) if idx < len(row) else ""
            w = col_widths[idx]
            v_w = str_width(val)
            pad = " " * max(0, w - v_w)
            if alignments and idx < len(alignments) and alignments[idx] == "right":
                r_parts.append(pad + val)
            else:
                r_parts.append(val + pad)
        out_lines.append("  " + "   ".join(r_parts))
    
    return "\n".join(out_lines)


def format_prompt(text, default=None):
    """Render a clean, modern input prompt."""
    clean_text = text.lstrip("\n")
    arrow = bold_cyan("›")
    if default is not None:
        def_str = dim(f"[{default}]")
        return f"{arrow} {bold(clean_text)} {def_str}: "
    return f"{arrow} {bold(clean_text)}: "
