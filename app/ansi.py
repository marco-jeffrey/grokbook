"""Convert ANSI escape sequences to HTML spans."""

import re

_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

_FG_COLORS = {
    30: "#6e7681", 31: "#ff7b72", 32: "#7ee787", 33: "#d29922",
    34: "#79c0ff", 35: "#d2a8ff", 36: "#a5d6ff", 37: "#c9d1d9",
    90: "#8b949e", 91: "#ffa198", 92: "#9be9a8", 93: "#e3b341",
    94: "#a5d6ff", 95: "#d2a8ff", 96: "#b6e3ff", 97: "#f0f6fc",
}

_BG_COLORS = {
    40: "#6e7681", 41: "#ff7b72", 42: "#7ee787", 43: "#d29922",
    44: "#79c0ff", 45: "#d2a8ff", 46: "#a5d6ff", 47: "#c9d1d9",
    100: "#8b949e", 101: "#ffa198", 102: "#9be9a8", 103: "#e3b341",
    104: "#a5d6ff", 105: "#d2a8ff", 106: "#b6e3ff", 107: "#f0f6fc",
}


def ansi_to_html(text: str) -> str:
    """Convert ANSI escape sequences in text to HTML with inline styles.

    Returns HTML string with <span style="..."> tags. The output is
    intended to be wrapped in SafeString() for rendering.
    All non-ANSI text is HTML-escaped.
    """
    import html as _html

    result = []
    pos = 0
    span_open = False
    current_styles: dict[str, str] = {}

    for match in _ANSI_RE.finditer(text):
        # Append text before this escape sequence (HTML-escaped)
        if match.start() > pos:
            result.append(_html.escape(text[pos:match.start()]))
        pos = match.end()

        codes_str = match.group(1)
        if not codes_str:
            codes = [0]
        else:
            codes = [int(c) for c in codes_str.split(";") if c]

        for code in codes:
            if code == 0:
                current_styles.clear()
            elif code == 1:
                current_styles["font-weight"] = "bold"
            elif code == 3:
                current_styles["font-style"] = "italic"
            elif code == 4:
                current_styles["text-decoration"] = "underline"
            elif code in _FG_COLORS:
                current_styles["color"] = _FG_COLORS[code]
            elif code in _BG_COLORS:
                current_styles["background-color"] = _BG_COLORS[code]

        # Close previous span if open
        if span_open:
            result.append("</span>")
            span_open = False

        # Open new span if we have styles
        if current_styles:
            style = ";".join(f"{k}:{v}" for k, v in current_styles.items())
            result.append(f'<span style="{style}">')
            span_open = True

    # Append remaining text
    if pos < len(text):
        result.append(_html.escape(text[pos:]))

    if span_open:
        result.append("</span>")

    return "".join(result)
