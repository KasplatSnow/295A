#!/usr/bin/env python3
"""
PR-01 (addendum): BEATs automatic asset downloader.

Downloads:
  1. BEATs source files (BEATs.py + Tokenizers.py) from GitHub raw URLs.
  2. BEATs fine-tuned checkpoint from HuggingFace Hub or a direct URL.

Configuration priority for checkpoint:
  1. Env var  AI_BEATS_DOWNLOAD_URL    — direct HTTPS URL (overrides everything)
  2. Env var  AI_BEATS_MODEL_PATH      — destination path for the checkpoint
  3. Env var  AI_BEATS_HF_REPO_ID      — HuggingFace repo (default: agkphysics/AudioSet-BEATs)
  4. Config   models.audio_anomaly.hf_repo_id / hf_filename in models.yaml

Usage (standalone):
    python scripts/download_beats.py
    python scripts/download_beats.py --checkpoint-only
    python scripts/download_beats.py --source-only
    python scripts/download_beats.py --dest models/audio/beats/BEATs_custom.pt

Exit codes:
    0 — all downloads succeeded (or already present)
    1 — at least one download failed
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# ── Path anchors ──────────────────────────────────────────────────────────────
# This script lives at services/ai/scripts/download_beats.py.
# _SCRIPT_DIR = services/ai/scripts/
# _AI_ROOT    = services/ai/
_SCRIPT_DIR = Path(__file__).resolve().parent
_AI_ROOT = _SCRIPT_DIR.parent

# Default locations matching the plan Section 5.1
_DEFAULT_BEATS_SRC_DIR = _AI_ROOT / "third_party" / "beats"
_DEFAULT_BEATS_MODEL_DIR = _AI_ROOT / "models" / "audio" / "beats"
_DEFAULT_CHECKPOINT_NAME = "BEATs_iter3_plus_AS2M_finetuned_cpt2.pt"
_DEFAULT_CHECKPOINT_PATH = Path(
    os.environ.get(
        "AI_BEATS_MODEL_PATH",
        str(_DEFAULT_BEATS_MODEL_DIR / _DEFAULT_CHECKPOINT_NAME),
    )
)

# ── BEATs source file URLs (GitHub raw — stable, no auth, no expiry) ─────────
_BEATS_BASE_URL = "https://raw.githubusercontent.com/microsoft/unilm/master/beats"
_BEATS_SOURCE_FILES = {
    "BEATs.py":      f"{_BEATS_BASE_URL}/BEATs.py",
    "Tokenizers.py": f"{_BEATS_BASE_URL}/Tokenizers.py",
    "backbone.py":   f"{_BEATS_BASE_URL}/backbone.py",
    "modules.py":    f"{_BEATS_BASE_URL}/modules.py",
    "quantizer.py":  f"{_BEATS_BASE_URL}/quantizer.py",
}

# ── Default HuggingFace source for checkpoint ─────────────────────────────────
# The agkphysics/AudioSet-BEATs mirror is a community-maintained public repo
# that hosts the official Microsoft BEATs checkpoints and is resolvable via
# huggingface_hub without authentication.
_DEFAULT_HF_REPO_ID = os.environ.get(
    "AI_BEATS_HF_REPO_ID", "agkphysics/AudioSet-BEATs"
)
_DEFAULT_HF_FILENAME = os.environ.get(
    "AI_BEATS_HF_FILENAME", "BEATs_iter3_plus_AS2M_finetuned_cpt2.pt"
)

# Direct URL override (takes priority over HuggingFace)
_DIRECT_URL = os.environ.get("AI_BEATS_DOWNLOAD_URL", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def _print_warn(msg: str) -> None:
    print(f"  \033[33m⚠\033[0m {msg}", file=sys.stderr)


def _print_err(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}", file=sys.stderr)


def _download_url(url: str, dest: Path, label: str) -> bool:
    """Download url → dest using urllib (no extra deps). Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    print(f"    Downloading {label} …")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VigilZone/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 1 << 20  # 1 MB
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                fh.write(data)
                downloaded += len(data)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded / 1_048_576
                    print(f"\r    {pct:3d}%  {mb:.1f} MB / {total / 1_048_576:.1f} MB", end="", flush=True)
        print()  # newline after progress
        tmp.rename(dest)
        return True
    except Exception as exc:
        _print_err(f"Download failed for {label}: {exc}")
        if tmp.exists():
            tmp.unlink()
        return False


def _download_hf(repo_id: str, filename: str, dest: Path) -> bool:
    """Download a file from HuggingFace Hub into dest. Returns True on success."""
    try:
        from huggingface_hub import hf_hub_download  # type: ignore[import]
    except ImportError:
        _print_warn("huggingface_hub not installed; install with: pip install huggingface_hub>=0.20.0")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"    Downloading {filename} from HuggingFace ({repo_id}) …")
    try:
        cached = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(dest.parent),
            local_dir_use_symlinks=False,
        )
        # hf_hub_download with local_dir places the file at dest.parent/filename
        downloaded = dest.parent / filename
        if downloaded.resolve() != dest.resolve() and downloaded.exists():
            downloaded.rename(dest)
        return dest.exists()
    except Exception as exc:
        _print_err(f"HuggingFace download failed ({repo_id}/{filename}): {exc}")
        return False


# ── Main tasks ────────────────────────────────────────────────────────────────

def download_beats_source(src_dir: Path) -> bool:
    """Download BEATs.py and Tokenizers.py from GitHub raw URLs."""
    src_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for fname, url in _BEATS_SOURCE_FILES.items():
        dest = src_dir / fname
        if dest.exists():
            _print_ok(f"{fname} already present — skip")
            continue
        ok = _download_url(url, dest, fname)
        if ok:
            _print_ok(f"{fname} downloaded → {dest.relative_to(_AI_ROOT)}")
        else:
            all_ok = False
    return all_ok


def download_beats_checkpoint(
    model_dir: Path,
    checkpoint_name: str,
    direct_url: str = "",
    hf_repo_id: str = _DEFAULT_HF_REPO_ID,
    hf_filename: str = _DEFAULT_HF_FILENAME,
) -> bool:
    """Download the BEATs fine-tuned checkpoint. Returns True if file is present."""
    dest = model_dir / checkpoint_name
    if dest.exists():
        size_mb = dest.stat().st_size / 1_048_576
        _print_ok(f"{checkpoint_name} already present ({size_mb:.0f} MB) — skip")
        return True

    model_dir.mkdir(parents=True, exist_ok=True)

    # Strategy 1: direct URL via env var
    if direct_url:
        print(f"  Using direct URL from AI_BEATS_DOWNLOAD_URL")
        ok = _download_url(direct_url, dest, checkpoint_name)
        if ok:
            _print_ok(f"Checkpoint downloaded → {dest.relative_to(_AI_ROOT)}")
            return True
        _print_warn("Direct URL download failed; trying HuggingFace …")

    # Strategy 2: HuggingFace Hub
    ok = _download_hf(hf_repo_id, hf_filename, dest)
    if ok:
        _print_ok(f"Checkpoint downloaded → {dest.relative_to(_AI_ROOT)}")
        return True

    _print_err(
        f"Could not download checkpoint automatically.\n"
        f"  Manual option A: set env var AI_BEATS_DOWNLOAD_URL=<direct-https-url>\n"
        f"  Manual option B: download from https://github.com/microsoft/unilm/tree/master/beats\n"
        f"                   and place at: {dest}"
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-download BEATs source files and checkpoint for VigilZone."
    )
    parser.add_argument(
        "--checkpoint-only",
        action="store_true",
        help="Only download the checkpoint (.pt), skip source files.",
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Only download BEATs.py / Tokenizers.py, skip checkpoint.",
    )
    parser.add_argument(
        "--dest",
        default=str(_DEFAULT_CHECKPOINT_PATH),
        help=f"Destination path for checkpoint (default: {_DEFAULT_CHECKPOINT_PATH})",
    )
    parser.add_argument(
        "--beats-src",
        default=str(_DEFAULT_BEATS_SRC_DIR),
        help=f"Destination directory for BEATs source files (default: {_DEFAULT_BEATS_SRC_DIR})",
    )
    parser.add_argument(
        "--hf-repo-id",
        default=_DEFAULT_HF_REPO_ID,
        help=f"HuggingFace repo ID for checkpoint (default: {_DEFAULT_HF_REPO_ID})",
    )
    parser.add_argument(
        "--hf-filename",
        default=_DEFAULT_HF_FILENAME,
        help=f"HuggingFace filename (default: {_DEFAULT_HF_FILENAME})",
    )
    args = parser.parse_args()

    dest = Path(args.dest).resolve()
    beats_src = Path(args.beats_src).resolve()

    print("=" * 60)
    print("BEATs Asset Downloader — VigilZone")
    print("=" * 60)

    all_ok = True

    # ── Source files ──────────────────────────────────────────────────
    if not args.checkpoint_only:
        print("\n[1/2] BEATs source files (BEATs.py, Tokenizers.py)")
        ok = download_beats_source(beats_src)
        if not ok:
            all_ok = False

    # ── Checkpoint ────────────────────────────────────────────────────
    if not args.source_only:
        print(f"\n[2/2] BEATs checkpoint: {dest.name}")
        ok = download_beats_checkpoint(
            model_dir=dest.parent,
            checkpoint_name=dest.name,
            direct_url=_DIRECT_URL,
            hf_repo_id=args.hf_repo_id,
            hf_filename=args.hf_filename,
        )
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("=" * 60)
        print("All BEATs assets ready.")
        print()
        print("Next step — verify the checkpoint:")
        rel_model = dest.relative_to(_AI_ROOT) if dest.is_relative_to(_AI_ROOT) else dest
        rel_src   = beats_src.relative_to(_AI_ROOT) if beats_src.is_relative_to(_AI_ROOT) else beats_src
        print(f"  python scripts/verify_beats_checkpoint.py \\")
        print(f"      --model-path {rel_model} \\")
        print(f"      --beats-src  {rel_src}")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("Some assets failed to download — see errors above.")
        print("Set AI_BEATS_DOWNLOAD_URL to a direct checkpoint URL as a fallback.")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
