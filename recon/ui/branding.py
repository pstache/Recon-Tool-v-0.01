"""
SpareBank 1 brand tokens + CSS that Streamlit's theme system can't express.

The design-system colors and tone of voice come from
`/mnt/skills/user/sparebank1-design-system/SKILL.md`. This module is the
single place where those tokens become Python constants and CSS — every
page imports from here rather than hardcoding hex values.

What lives here vs in `.streamlit/config.toml`:

  config.toml — global palette: primaryColor, background, body text, default
                font. Streamlit applies these to all built-in widgets.
  branding.py — everything config.toml can't reach:
                  * rounded corners (avrundet rektangulær per design system)
                  * sidebar accent + section divider color
                  * status pills (matched / partial / unmatched)
                  * the web-font import (Nunito as Title fallback)
                  * helper functions for status badges in tables

If a future change shifts to the official "SpareBank 1" / "SpareBank 1 Title"
fonts (e.g. via an internal CDN), only the @import URL in INJECTED_CSS needs
to change.
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------- color tokens

# Primærpalett
VANN     = "#005AA4"   # primary — must be the most visible color on screen
FJELL    = "#002776"   # darker blue for emphasis, headers, deep accents
SAND     = "#F8E9DD"   # warm beige for secondary surfaces
FROST    = "#7EB5D2"   # light blue tint
SYRIN    = "#D3D3EA"   # soft lilac

# Nøytrale farger
NATT     = "#001032"   # body text, dark surfaces
KOKSGRA  = "#323232"   # secondary text
MORK_GRA = "#676767"   # tertiary / hints
GRA      = "#ADADAD"   # borders, dividers
LYS_GRA  = "#D8D8D8"   # subtle backgrounds
HVIT     = "#FFFFFF"

# Støttefarger (used semantically for status — see SEMANTIC_* below)
NORDLYS  = "#33AF85"   # green — success / auto-matched
BAR      = "#E44244"   # red — error / unmatched-with-issue
SOL      = "#DC8000"   # orange — warning / partial


# ---------------------------------------------------------------- semantic mapping

# Status -> color. The design system reserves support colors (Nordlys, Bær,
# Sol) for "local character / illustrations". Using them here is a pragmatic
# extension: status badges need an at-a-glance traffic-light read, and these
# are the closest brand-aligned green/red/orange we have. They're applied
# only on small badges, never on large surfaces — so the page still reads as
# blue-dominant.
SEMANTIC_COLORS: dict[str, str] = {
    "auto_matched":      NORDLYS,
    "manual_matched":    VANN,
    "partial":           SOL,
    "unmatched":         GRA,
    "known_unmatchable": KOKSGRA,
}


# ---------------------------------------------------------------- CSS

# Notes on the choices below:
#
#  * Border-radius 16px on buttons and inputs reflects the "avrundet
#    rektangulær" half-radius variant from the design system. Streamlit's
#    default 8px is too sharp for the SB1 formspråk.
#  * Sidebar uses Sand (secondaryBackgroundColor) per config.toml. We add
#    a 4px Vann left border on the active page to give it brand presence.
#  * Headings get Fjell (deeper blue) per the PowerPoint guidance: "Title
#    text: color Vann or Fjell". H1 uses Fjell for highest emphasis; H2/H3
#    use Vann.
#  * The web-font import loads Nunito (closest free geometric sans with the
#    circular letterform character of the official SB1 typefaces). If/when
#    the official fonts are hosted on an internal CDN, swap the URL.

INJECTED_CSS = f"""
<style>
  /* ---- Type: closest free fallback to the SB1 circular letterform ---- */
  @import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;500;600;700&display=swap');

  html, body, [class*="css"] {{
      font-family: 'Nunito', system-ui, -apple-system, "Segoe UI", sans-serif;
  }}

  /* ---- Headings: SB1 Title-style, Fjell/Vann per the design system ---- */
  h1 {{ color: {FJELL}; font-weight: 600; letter-spacing: -0.01em; }}
  h2 {{ color: {VANN}; font-weight: 600; }}
  h3 {{ color: {VANN}; font-weight: 600; }}

  /* ---- Buttons: avrundet rektangulær (half-radius) ---- */
  .stButton > button,
  .stDownloadButton > button,
  [data-testid="stFormSubmitButton"] > button {{
      border-radius: 16px;
      font-weight: 600;
      padding: 0.5rem 1.25rem;
      border: 1px solid transparent;
      transition: all 0.15s ease;
  }}
  .stButton > button[kind="primary"],
  .stDownloadButton > button[kind="primary"],
  [data-testid="stFormSubmitButton"] > button[kind="primary"] {{
      background: {VANN};
      color: {HVIT};
  }}
  .stButton > button[kind="primary"]:hover,
  .stDownloadButton > button[kind="primary"]:hover,
  [data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {{
      background: {FJELL};
  }}
  .stButton > button[kind="secondary"]:hover {{
      border-color: {VANN};
      color: {VANN};
  }}

  /* ---- Inputs and selects: rounded ---- */
  .stTextInput input,
  .stNumberInput input,
  .stDateInput input,
  .stSelectbox > div > div,
  .stMultiSelect > div > div {{
      border-radius: 12px;
  }}

  /* ---- Sidebar: subtle Vann border-right gives brand presence ---- */
  [data-testid="stSidebar"] {{
      border-right: 4px solid {VANN};
  }}
  [data-testid="stSidebar"] h1,
  [data-testid="stSidebar"] .stMarkdown h1 {{
      color: {FJELL};
      font-size: 1.6rem;
      margin-bottom: 0.25rem;
  }}

  /* ---- Sidebar metrics: tighter, branded ---- */
  [data-testid="stSidebar"] [data-testid="stMetric"] {{
      background: {HVIT};
      padding: 0.5rem 0.75rem;
      border-radius: 12px;
      margin-bottom: 0.5rem;
  }}
  [data-testid="stSidebar"] [data-testid="stMetricLabel"] {{
      color: {KOKSGRA};
      font-size: 0.75rem;
  }}
  [data-testid="stSidebar"] [data-testid="stMetricValue"] {{
      color: {FJELL};
      font-weight: 600;
  }}

  /* ---- Tabs: Vann underline on active ---- */
  .stTabs [data-baseweb="tab-list"] {{
      gap: 0.25rem;
      border-bottom: 1px solid {LYS_GRA};
  }}
  .stTabs [data-baseweb="tab"] {{
      font-weight: 500;
      color: {KOKSGRA};
  }}
  .stTabs [aria-selected="true"] {{
      color: {VANN} !important;
      border-bottom-color: {VANN} !important;
  }}

  /* ---- Expanders: rounded, subtle ---- */
  details[data-testid="stExpander"] {{
      border-radius: 16px;
      border: 1px solid {LYS_GRA};
  }}
  details[data-testid="stExpander"] summary {{
      font-weight: 500;
      color: {FJELL};
  }}

  /* ---- Dataframes: gentler corners ---- */
  [data-testid="stDataFrame"] {{
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid {LYS_GRA};
  }}

  /* ---- Status pills (used inline in markdown via sb1_status_pill) ---- */
  .sb1-pill {{
      display: inline-block;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      color: {HVIT};
  }}

  /* ---- Header banner ---- */
  .sb1-header {{
      background: linear-gradient(135deg, {VANN} 0%, {FJELL} 100%);
      color: {HVIT};
      padding: 1.25rem 1.5rem;
      border-radius: 16px;
      margin-bottom: 1.5rem;
  }}
  .sb1-header h1 {{
      color: {HVIT};
      margin: 0;
      font-size: 1.75rem;
  }}
  .sb1-header p {{
      color: {SAND};
      margin: 0.25rem 0 0 0;
      font-size: 0.95rem;
  }}

  /* ---- Hide the 'Deploy' button (internal tool, not Streamlit demo) ---- */
  [data-testid="stToolbar"] button[kind="header"] {{
      display: none;
  }}
</style>
"""


def apply_branding() -> None:
    """Inject SB1 styling. Call once near the top of `app.py:main()`.

    Calling more than once is a no-op visually but produces redundant <style>
    tags in the DOM, so callers should call it exactly once per session.
    """
    st.markdown(INJECTED_CSS, unsafe_allow_html=True)


def sb1_header(title: str, subtitle: str | None = None) -> None:
    """Render the SB1 gradient page header.

    Use this once per page in place of `st.title()`. The gradient (Vann ->
    Fjell) is the design system's recommended Vann + Fjell two-color combo,
    used here to anchor each page in the brand without overwhelming the
    content area below.
    """
    sub = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="sb1-header"><h1>{title}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )


def sb1_status_pill(status: str, label: str | None = None) -> str:
    """Return a small inline-styled pill for a match status.

    Usage in markdown:
        st.markdown(sb1_status_pill('auto_matched'), unsafe_allow_html=True)
    """
    color = SEMANTIC_COLORS.get(status, GRA)
    text = label or status.replace("_", " ")
    return f'<span class="sb1-pill" style="background:{color}">{text}</span>'
