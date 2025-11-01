#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse

def is_url(s: str) -> bool:
    try:
        return urlparse(s).scheme in {"http", "https"}
    except Exception:
        return False

def ensure_wkhtmltopdf():
    exe = shutil.which("wkhtmltopdf")
    if exe:
        return exe
    # Common Windows install paths fallback
    candidates = [
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
        r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

def main():
    parser = argparse.ArgumentParser(
        description="Convert an HTML file or URL to PDF using wkhtmltopdf."
    )
    parser.add_argument("input", help="Path to a local HTML file OR an http(s) URL.")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output PDF path (default: derive from input, e.g., page.html -> page.pdf)."
    )
    parser.add_argument(
        "--page-size", default="A4",
        help="Page size (default: A4). Examples: A4, Letter."
    )
    parser.add_argument(
        "--orientation", default="Portrait",
        choices=["Portrait", "Landscape"],
        help="Page orientation (default: Portrait)."
    )
    parser.add_argument(
        "--margin", default="10mm",
        help="Uniform page margin (default: 10mm). Accepts e.g. 15mm, 0.5in."
    )
    parser.add_argument(
        "--wait", type=int, default=0,
        help="Wait this many milliseconds for JS-rendered pages before printing (default: 0)."
    )
    parser.add_argument(
        "--disable-smart-shrinking", action="store_true",
        help="Disable smart shrinking (useful if fonts/layout look squashed)."
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress wkhtmltopdf console output."
    )

    args = parser.parse_args()

    wkhtml = ensure_wkhtmltopdf()
    if not wkhtml:
        print(
            "ERROR: 'wkhtmltopdf' not found.\n"
            "• Install from https://wkhtmltopdf.org/downloads.html\n"
            "• On Windows, run the installer and re-open your terminal.\n"
            "• On macOS: brew install wkhtmltopdf\n"
            "• On Linux (Debian/Ubuntu): sudo apt-get install wkhtmltopdf\n",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate input & decide output
    src = args.input
    if not is_url(src):
        if not os.path.exists(src):
            print(f"ERROR: File not found: {src}", file=sys.stderr)
            sys.exit(1)
        if not args.output:
            base, _ = os.path.splitext(src)
            out = base + ".pdf"
        else:
            out = args.output
    else:
        out = args.output or "output.pdf"

    # Build wkhtmltopdf command
    cmd = [
        wkhtml,
        "--page-size", args.page_size,
        "--orientation", args.orientation,
        "--margin-top", args.margin,
        "--margin-right", args.margin,
        "--margin-bottom", args.margin,
        "--margin-left", args.margin,
        "--enable-local-file-access",  # allows local CSS/images to load for file inputs
        "--print-media-type",          # use print CSS if provided
    ]

    if args.wait and args.wait > 0:
        cmd += ["--javascript-delay", str(args.wait)]

    if args.disable_smart_shrinking:
        cmd += ["--disable-smart-shrinking"]

    if args.quiet:
        cmd += ["-q"]

    cmd += [src, out]

    # Run
    try:
        proc = subprocess.run(cmd, capture_output=args.quiet)
        if proc.returncode != 0:
            # Show stderr if not quiet; otherwise at least surface the error    
            if args.quiet:
                print("wkhtmltopdf failed. Re-run without --quiet to see details.", file=sys.stderr)
            else:
                sys.stderr.write(proc.stderr.decode(errors="ignore"))
            sys.exit(proc.returncode)
        else:
            print(f"PDF created: {out}")
    except FileNotFoundError:
        print("ERROR: wkhtmltopdf executable not found at runtime.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
