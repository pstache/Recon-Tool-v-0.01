"""
Institution reference data and canonical-key resolution.

The canonical key (`institution_key`) is what the matching engine joins on.
Three different sources spell the same legal entity differently — this module
collapses them all to one key.

The defining example: post-merger 'SPAREBANK 1 SØR-NORGE ASA' is the legal
successor to both 'SPAREBANK 1 SR' (Allfunds code 3229) and
'SPAREBANK 1 SØRØST-NORGE' (Allfunds code 2489). All three names and both
codes must resolve to the same key (`sb1_sor_norge`).

This is intentionally implemented as a function (not a join table) for two
reasons: (a) it has to handle name variations and casing/whitespace from messy
source data, and (b) it needs an explicit unknown branch so we surface
unrecognized institutions rather than silently producing garbage keys.

If new SpareBank 1 entities or new ODIN spellings appear, add them to
NAME_TO_KEY and CODE_TO_KEY below — those are the only two things to edit.
The DB-table-editable-from-UI requirement (answer #21) is implemented in
storage.repository as a layer on top of these defaults.
"""

from __future__ import annotations

# Allfunds 4-digit portfolio codes -> canonical key.
# 3229 (SR) and 2489 (SØRØST-NORGE) BOTH map to sb1_sor_norge per Q&A.
CODE_TO_KEY: dict[str, str] = {
    "1084": "sb1_ostfold_akershus",
    "1373": "sb1_forvaltning",
    "1801": "sb1_ostlandet",
    "2085": "sb1_lom_og_skjak",
    "2096": "sb1_gudbrandsdal",
    "2289": "sb1_ringerike_hadeland",
    "2329": "sb1_hallingdal_valdres",
    "2489": "sb1_sor_norge",          # SØRØST-NORGE pre-merger -> SØR-NORGE
    "3229": "sb1_sor_norge",          # SR pre-merger -> SØR-NORGE
    "3702": "sb1_sogn_og_fjordane",
    "3920": "sb1_nordmore",
    "4210": "sb1_smn",
    "4519": "sb1_helgeland",
    "4702": "sb1_nord_norge",
    "8700": "sb1_investeringsradgivning",
}

# Pre-canonicalised display name (UPPER, single-spaced) -> canonical key.
# Add every spelling we expect to see from any source here.
NAME_TO_KEY: dict[str, str] = {
    "SPAREBANK 1 ØSTFOLD AKERSHUS":     "sb1_ostfold_akershus",
    "SPAREBANK 1 FORVALTNING":          "sb1_forvaltning",
    "SPAREBANK 1 ØSTLANDET":            "sb1_ostlandet",
    "SPAREBANK 1 LOM OG SKJÅK":         "sb1_lom_og_skjak",
    "SPAREBANK 1 GUDBRANDSDAL":         "sb1_gudbrandsdal",
    "SPAREBANK 1 RINGERIKE HADELAND":   "sb1_ringerike_hadeland",
    "SPAREBANK 1 HALLINGDAL VALDRES":   "sb1_hallingdal_valdres",
    "SPAREBANK 1 SØRØST-NORGE":         "sb1_sor_norge",
    "SPAREBANK 1 SR":                   "sb1_sor_norge",
    "SPAREBANK 1 SØR-NORGE ASA":        "sb1_sor_norge",
    "SPAREBANK 1 SOGN OG FJORDANE":     "sb1_sogn_og_fjordane",
    "SPAREBANK 1 NORDMØRE":             "sb1_nordmore",
    "SPAREBANK 1 SMN":                  "sb1_smn",
    "SPAREBANK 1 HELGELAND":            "sb1_helgeland",
    "SPAREBANK 1 NORD-NORGE":           "sb1_nord_norge",
    "SPAREBANK 1 INVESTERINGSRÅDGIVNING": "sb1_investeringsradgivning",
}

# Display name to show in the UI for each canonical key.
KEY_TO_DISPLAY: dict[str, str] = {
    "sb1_ostfold_akershus":         "SpareBank 1 Østfold Akershus",
    "sb1_forvaltning":              "SpareBank 1 Forvaltning",
    "sb1_ostlandet":                "SpareBank 1 Østlandet",
    "sb1_lom_og_skjak":             "SpareBank 1 Lom og Skjåk",
    "sb1_gudbrandsdal":             "SpareBank 1 Gudbrandsdal",
    "sb1_ringerike_hadeland":       "SpareBank 1 Ringerike Hadeland",
    "sb1_hallingdal_valdres":       "SpareBank 1 Hallingdal Valdres",
    "sb1_sor_norge":                "SpareBank 1 Sør-Norge ASA",
    "sb1_sogn_og_fjordane":         "SpareBank 1 Sogn og Fjordane",
    "sb1_nordmore":                 "SpareBank 1 Nordmøre",
    "sb1_smn":                      "SpareBank 1 SMN",
    "sb1_helgeland":                "SpareBank 1 Helgeland",
    "sb1_nord_norge":               "SpareBank 1 Nord-Norge",
    "sb1_investeringsradgivning":   "SpareBank 1 Investeringsrådgivning",
}


def normalize_name(name: str | None) -> str:
    """Upper-case, collapse whitespace. Strip BOMs and stray non-breaking spaces."""
    if name is None:
        return ""
    n = name.replace("\u00a0", " ").replace("\ufeff", "")
    return " ".join(n.upper().split())


def key_from_code(code: str | None) -> str | None:
    """Allfunds 4-digit portfolio code -> canonical key. None on miss."""
    if code is None:
        return None
    c = code.strip()
    return CODE_TO_KEY.get(c)


def key_from_name(name: str | None) -> str | None:
    """Genus/ODIN display name -> canonical key. None on miss."""
    return NAME_TO_KEY.get(normalize_name(name))


def display_for_key(key: str | None) -> str:
    """Pretty name for the UI. Falls back to the key itself."""
    if key is None:
        return ""
    return KEY_TO_DISPLAY.get(key, key)


def all_known_keys() -> list[str]:
    return sorted(KEY_TO_DISPLAY.keys())
