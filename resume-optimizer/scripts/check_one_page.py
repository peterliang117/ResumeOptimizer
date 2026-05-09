#!/usr/bin/env python3
"""Export a DOCX to PDF with LibreOffice and check the PDF page count."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def find_soffice() -> str:
    mac_path = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac_path.exists():
        return str(mac_path)
    soffice = shutil.which("soffice")
    if soffice:
        return soffice
    raise SystemExit("LibreOffice not found. Install it with: brew install --cask libreoffice")


def convert_to_pdf(docx: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    command = [
        find_soffice(),
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(docx),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout or "LibreOffice PDF conversion failed.")
    pdf = outdir / f"{docx.stem}.pdf"
    if not pdf.exists():
        raise SystemExit(f"Expected PDF was not created: {pdf}")
    return pdf


def count_pdf_pages(pdf: Path) -> int:
    try:
        import fitz
    except ImportError as exc:
        raise SystemExit("Install PyMuPDF to count PDF pages.") from exc

    with fitz.open(pdf) as document:
        return document.page_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether a DOCX exports to a one-page PDF.")
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("outputs"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.docx.exists():
        raise SystemExit(f"DOCX not found: {args.docx}")

    pdf = convert_to_pdf(args.docx, args.outdir)
    pages = count_pdf_pages(pdf)
    print(f"{pdf}: {pages} page(s)")
    if pages > 1:
        print("Resume exceeds one page. Shorten accepted edits or remove lower-priority bullets.")
        return 2
    print("Resume fits on one page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
