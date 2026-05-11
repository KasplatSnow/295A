#!/usr/bin/env python3
"""
PR-01: BEATs checkpoint verification script.

Defaults are auto-detected from this script's own location — it works
correctly from ANY working directory (backend dir, repo root, etc.).

Usage:
    # No arguments needed if using standard directory layout:
    python services/ai/scripts/verify_beats_checkpoint.py

    # Or with explicit paths:
    python scripts/verify_beats_checkpoint.py \\
        --model-path models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt

    # Auto-download missing assets then verify:
    python scripts/verify_beats_checkpoint.py --auto-download

    # Docker container:
    python scripts/verify_beats_checkpoint.py \\
        --model-path /app/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt

Exit codes:
    0 — checkpoint verified and inference test passed
    1 — general error
    2 — checkpoint file not found
    3 — missing required checkpoint keys (cfg, model)
    4 — BEATs source files not found in --beats-src
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Path anchors: script lives at services/ai/scripts/verify_beats_checkpoint.py
# so AI root is always two levels up — works from any CWD.
_SCRIPT_DIR = Path(__file__).resolve().parent   # services/ai/scripts/
_AI_ROOT    = _SCRIPT_DIR.parent                # services/ai/

_DEFAULT_BEATS_SRC     = str(_AI_ROOT / "third_party" / "beats")
_DEFAULT_MODEL_PATH    = str(_AI_ROOT / "models" / "audio" / "beats"
                             / "BEATs_iter3_plus_AS2M_finetuned_cpt2.pt")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a BEATs fine-tuned checkpoint for VigilZone."
    )
    parser.add_argument(
        "--model-path",
        default=_DEFAULT_MODEL_PATH,
        help=f"Path to BEATs checkpoint (default: auto-detected → {_DEFAULT_MODEL_PATH})",
    )
    parser.add_argument(
        "--beats-src",
        default=_DEFAULT_BEATS_SRC,
        help=f"Directory containing BEATs.py and Tokenizers.py (default: auto-detected → {_DEFAULT_BEATS_SRC})",
    )
    parser.add_argument(
        "--auto-download",
        action="store_true",
        help="Run download_beats.py automatically if assets are missing before verifying.",
    )
    args = parser.parse_args()

    # ── 0. Auto-download if requested ────────────────────────────────
    if args.auto_download:
        import subprocess
        dl_script = _SCRIPT_DIR / "download_beats.py"
        print(f"Running auto-download: {dl_script}")
        ret = subprocess.call([sys.executable, str(dl_script)])
        if ret != 0:
            print("ERROR: auto-download failed — see output above.", file=sys.stderr)
            return 1
        print()

    # ── 1. Check checkpoint file exists ───────────────────────────────
    model_path = Path(args.model_path)
    if not model_path.exists():
        print(
            f"ERROR: checkpoint not found: {model_path}\n"
            f"  Run: python {_SCRIPT_DIR / 'download_beats.py'}\n"
            f"  Or:  python {_SCRIPT_DIR / 'verify_beats_checkpoint.py'} --auto-download",
            file=sys.stderr,
        )
        return 2

    # ── 2. Check BEATs source files ───────────────────────────────────
    beats_src = Path(args.beats_src)
    beats_py = beats_src / "BEATs.py"
    tokenizers_py = beats_src / "Tokenizers.py"

    missing_src = [p for p in (beats_py, tokenizers_py) if not p.exists()]
    if missing_src:
        print(
            f"ERROR: BEATs source files not found: {[str(p) for p in missing_src]}\n"
            f"  Run: python {_SCRIPT_DIR / 'download_beats.py'} --source-only",
            file=sys.stderr,
        )
        return 4

    # ── 3. Import torch (must be available) ───────────────────────────
    try:
        import torch
    except ImportError:
        print(
            "ERROR: torch not installed. Run: pip install torch>=2.0.0",
            file=sys.stderr,
        )
        return 1

    # ── 4. Import BEATs from vendored source ──────────────────────────
    sys.path.insert(0, str(beats_src.resolve()))
    try:
        from BEATs import BEATs, BEATsConfig  # type: ignore[import]
    except ImportError as exc:
        print(
            f"ERROR: cannot import BEATs from {beats_src}: {exc}",
            file=sys.stderr,
        )
        return 1

    # ── 5. Load checkpoint ────────────────────────────────────────────
    print(f"Loading checkpoint: {model_path}")
    try:
        checkpoint = torch.load(str(model_path), map_location="cpu")
    except Exception as exc:
        print(f"ERROR: failed to load checkpoint: {exc}", file=sys.stderr)
        return 1

    required_keys = {"cfg", "model"}
    missing_keys = required_keys - set(checkpoint.keys())
    if missing_keys:
        print(
            f"ERROR: checkpoint is missing required keys: {sorted(missing_keys)}\n"
            "       Expected: 'cfg' (model config) and 'model' (state dict).\n"
            "       Ensure the file is the official BEATs fine-tuned checkpoint.",
            file=sys.stderr,
        )
        return 3

    # ── 6. Instantiate model ───────────────────────────────────────────
    print("Instantiating BEATs model...")
    try:
        cfg = BEATsConfig(checkpoint["cfg"])
        model = BEATs(cfg)
        model.load_state_dict(checkpoint["model"])
        model.eval()
    except Exception as exc:
        print(f"ERROR: model instantiation failed: {exc}", file=sys.stderr)
        return 1

    # ── 7. Check label_dict ────────────────────────────────────────────
    label_dict = checkpoint.get("label_dict")
    if label_dict is None:
        print(
            "WARNING: checkpoint has no 'label_dict'. "
            "Classification labels will be unavailable — "
            "only the pre-trained embedding output will work."
        )
    else:
        print(f"OK: label_dict has {len(label_dict)} entries")
        # Show a few example labels so the user can verify they look like AudioSet
        sample_labels = list(label_dict.values())[:5]
        print(f"    Sample labels: {sample_labels}")

    # ── 8. Forward pass smoke test ────────────────────────────────────
    print("Running forward pass smoke test (1 second of 16 kHz mono audio)...")
    try:
        # Exactly as specified in plan Section 5.1:
        #   shape: [batch, samples], dtype: float32, sample_rate: 16000, channels: 1
        audio = torch.randn(1, 16000)          # 1 second at 16 kHz
        padding_mask = torch.zeros(1, 16000).bool()

        with torch.no_grad():
            out = model.extract_features(audio, padding_mask=padding_mask)[0]

        print(f"OK: output shape = {tuple(out.shape)}")
        if label_dict:
            num_labels = len(label_dict)
            if out.shape[-1] == num_labels:
                print(f"OK: output dim ({out.shape[-1]}) matches label_dict size ({num_labels})")
            else:
                print(
                    f"WARNING: output dim {out.shape[-1]} != label_dict size {num_labels}. "
                    "Check checkpoint/model version match."
                )
    except Exception as exc:
        print(f"ERROR: forward pass failed: {exc}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print("OK: BEATs checkpoint verified successfully.")
    print(f"    Checkpoint : {model_path}")
    print(f"    BEATs src  : {beats_src}")
    if label_dict:
        print(f"    Label count: {len(label_dict)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
