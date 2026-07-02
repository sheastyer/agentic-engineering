"""Markdown → PDF for the artifacts posted into a run's Slack thread (PRD, research).

Deliberately lightweight: fpdf2 + markdown are pure-Python (no cairo/pango system
deps), which keeps the [slack] extra pip-installable anywhere the worker runs. The
trade-off is typography, not fidelity of content — fpdf2's core fonts are latin-1
only, so text is transliterated (smart quotes/dashes → ASCII, anything else
replaced). Returns None on any failure; the caller falls back to uploading the raw
markdown, so a rendering bug can never cost the artifact.
"""

import logging

_log = logging.getLogger(__name__)

# Common typographic characters Opus likes that latin-1 can't hold — mapped instead of
# mangled. Everything else unmappable degrades to '?' via encode(errors="replace").
_TRANSLITERATE = str.maketrans({
    "—": "--", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-",
    " ": " ", "→": "->", "←": "<-", "✓": "[x]", "✗": "[ ]",
})


def _latin1_safe(text: str) -> str:
    text = text.translate(_TRANSLITERATE)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def markdown_to_pdf(md: str, title: str) -> bytes | None:
    """Render markdown to PDF bytes, or None if rendering fails (caller degrades to
    uploading the markdown itself)."""
    try:
        import markdown as md_lib
        from fpdf import FPDF

        html = md_lib.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
        pdf = FPDF()
        pdf.set_title(_latin1_safe(title))
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()
        pdf.set_font("helvetica", size=11)
        pdf.write_html(_latin1_safe(html))
        return bytes(pdf.output())
    except Exception as exc:
        _log.warning("markdown->pdf render failed for %r: %s", title, exc)
        return None
