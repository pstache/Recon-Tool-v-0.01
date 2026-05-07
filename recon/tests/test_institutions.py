"""
Tests for reference.institutions.

Critical behaviours:
  * Sør-Norge merger: Allfunds codes 3229 (SR) and 2489 (SØRØST-NORGE) BOTH
    map to canonical key sb1_sor_norge. Genus/ODIN names 'SPAREBANK 1 SR',
    'SPAREBANK 1 SØRØST-NORGE', and 'SPAREBANK 1 SØR-NORGE ASA' also all map
    to sb1_sor_norge.
  * Unknown names / codes return None (caller handles routing to errors).
  * Whitespace/casing/non-breaking-space normalization for names.
"""

from __future__ import annotations

from reference.institutions import (
    key_from_code,
    key_from_name,
    display_for_key,
    normalize_name,
    all_known_keys,
)


# ---------------- Sør-Norge merger -------------------------------------------

def test_sor_norge_merger_via_codes():
    assert key_from_code("3229") == "sb1_sor_norge"   # SR
    assert key_from_code("2489") == "sb1_sor_norge"   # SØRØST-NORGE


def test_sor_norge_merger_via_names():
    assert key_from_name("SPAREBANK 1 SR") == "sb1_sor_norge"
    assert key_from_name("SPAREBANK 1 SØRØST-NORGE") == "sb1_sor_norge"
    assert key_from_name("SPAREBANK 1 SØR-NORGE ASA") == "sb1_sor_norge"


def test_sor_norge_displays_consistently():
    assert display_for_key("sb1_sor_norge") == "SpareBank 1 Sør-Norge ASA"


# ---------------- Other entities map correctly -------------------------------

def test_known_codes_resolve():
    assert key_from_code("4210") == "sb1_smn"
    assert key_from_code("4702") == "sb1_nord_norge"
    assert key_from_code("3920") == "sb1_nordmore"


def test_known_names_resolve():
    assert key_from_name("SPAREBANK 1 SMN") == "sb1_smn"
    assert key_from_name("SPAREBANK 1 NORD-NORGE") == "sb1_nord_norge"


# ---------------- Normalisation handles messy input --------------------------

def test_normalisation_collapses_whitespace_and_case():
    assert key_from_name("sparebank 1 smn") == "sb1_smn"
    assert key_from_name("SpareBank 1 SMN") == "sb1_smn"
    assert key_from_name("  SPAREBANK 1   SMN  ") == "sb1_smn"


def test_normalisation_strips_non_breaking_space():
    assert key_from_name("SPAREBANK\u00a01 SMN") == "sb1_smn"


def test_normalize_name_helpers():
    assert normalize_name("SpareBank   1\u00a0SMN") == "SPAREBANK 1 SMN"
    assert normalize_name(None) == ""


# ---------------- Unknown lookup returns None --------------------------------

def test_unknown_code_returns_none():
    assert key_from_code("9999") is None
    assert key_from_code("") is None
    assert key_from_code(None) is None


def test_unknown_name_returns_none():
    assert key_from_name("BOGUS BANK INC") is None
    assert key_from_name("") is None
    assert key_from_name(None) is None


# ---------------- Catalogue completeness -------------------------------------

def test_all_known_keys_includes_sor_norge_once():
    keys = all_known_keys()
    assert "sb1_sor_norge" in keys
    # The merger means SR and SØRØST-NORGE are NOT separate keys.
    assert "sb1_sr" not in keys
    assert "sb1_sorost_norge" not in keys


def test_display_falls_back_to_key_for_unknown():
    assert display_for_key("totally_unknown") == "totally_unknown"
    assert display_for_key(None) == ""
