"""Shared helpers used by multiple Streamlit pages."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import streamlit as st

from parsers.allfunds import parse_allfunds
from parsers.genus import parse_genus
from parsers.odin import parse_odin
from storage.db import DB, default_db_path
from storage.repository import FileAlreadyIngestedError, Repository


@st.cache_resource
def get_db() -> DB:
    """One DB connection for the whole Streamlit session.

    @cache_resource is the right primitive — Streamlit guarantees a single
    instance even across reruns. We DON'T close it because Streamlit owns
    the lifecycle.
    """
    db = DB()
    db.connect()
    return db


def get_repo() -> Repository:
    return Repository(get_db())


def detect_source(uploaded_filename: str) -> str | None:
    """Best-effort: name-based source detection.

    Genus internal exports are always .xlsx and named like
    'Genus_*_FundTransaction_*.xlsx'.
    ODIN exports are .xlsx and contain 'ODIN' or 'Transaksjonsbekreftelse'.
    Allfunds exports are .txt.
    """
    name = uploaded_filename.lower()
    if name.endswith(".txt") and "allfunds" in name:
        return "allfunds"
    if name.endswith(".xlsx"):
        if "genus" in name or "fundtransaction" in name:
            return "genus"
        if "odin" in name or "transaksjonsbekreftelse" in name:
            return "odin"
    return None


def parse_dispatch(path: Path, source: str) -> tuple[pl.DataFrame, list[dict]]:
    """Run the right parser for a given source. Returns (df, parse_errors)."""
    if source == "genus":
        return parse_genus(path), []
    if source == "odin":
        return parse_odin(path), []
    if source == "allfunds":
        return parse_allfunds(path)
    raise ValueError(f"Unknown source: {source!r}")


def file_already_uploaded(repo: Repository, path: Path) -> dict | None:
    """Check whether a file with identical bytes has been uploaded before."""
    h = repo.hash_file(path)
    return repo.file_already_ingested(h)


def save_uploaded_file(uploaded, target_dir: Path) -> Path:
    """Persist a Streamlit UploadedFile to disk so parsers can read it."""
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    target = target_dir / f"{timestamp}__{uploaded.name}"
    with open(target, "wb") as f:
        f.write(uploaded.getbuffer())
    return target


def format_count_pill(label: str, value: int, color: str = "#2E75B6") -> str:
    """Render a small inline-styled count pill (HTML)."""
    return (
        f'<span style="background:{color};color:white;padding:4px 10px;'
        f'border-radius:8px;font-weight:600;margin-right:8px;">'
        f'{label}: {value:,}</span>'
    )
