"""
Startup Doctor — runs BEFORE module initialization.

Responsibilities:
  A) Environment & GPU capability checks  (logged at startup)
  B) Config path normalization + search    (rewrite in-memory config)
  C) Deterministic auto-fetch              (Ultralytics / HF / git clone)
  D) Consolidated missing-asset report     (fail-fast with actionable list)

Usage:
    from src.runtime.doctor import Doctor
    report = Doctor.run_all(config)
    # report.gpu_usable, report.device_info, report.missing, …
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.log import setup_logger
from .device import DeviceInfo, select_device

logger = setup_logger("Doctor")

# Known Ultralytics weights that auto-download from their hub
_ULTRALYTICS_AUTO = {
    "yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt",
    "yolov8n-seg.pt", "yolov8s-seg.pt", "yolov8m-seg.pt",
    "yolov8n-cls.pt", "yolov8s-cls.pt", "yolov8m-cls.pt",
    "yolov12n.pt", "yolov12m.pt", "yolov12x.pt",
    "rtdetr-l.pt", "rtdetr-x.pt", "rtdetr-s.pt",
}

# RTDETRv2 official weights that can be auto-downloaded from GitHub
_RTDETRV2_WEIGHTS = {
    "rtdetrv2_r18vd_120e_coco_from_paddle.pth":  "https://github.com/lyuwenyu/storage/releases/download/v0.1/rtdetrv2_r18vd_120e_coco_from_paddle.pth",
    "rtdetrv2_r34vd_120e_coco_from_paddle.pth":  "https://github.com/lyuwenyu/storage/releases/download/v0.1/rtdetrv2_r34vd_120e_coco_from_paddle.pth",
    "rtdetrv2_r50vd_6x_coco_from_paddle.pth":    "https://github.com/lyuwenyu/storage/releases/download/v0.1/rtdetrv2_r50vd_6x_coco_from_paddle.pth",
    "rtdetrv2_r50vd_m_7x_coco_from_paddle.pth":  "https://github.com/lyuwenyu/storage/releases/download/v0.1/rtdetrv2_r50vd_m_7x_coco_from_paddle.pth",
    "rtdetrv2_r101vd_6x_coco_from_paddle.pth":   "https://github.com/lyuwenyu/storage/releases/download/v0.1/rtdetrv2_r101vd_6x_coco_from_paddle.pth",
}

# BEATs source files — downloaded from official GitHub raw URLs at startup.
# These are tiny Python files (~50 KB total); downloading them on first run
# is far more reliable than asking cloud operators to copy them manually.
_BEATS_BASE_URL = "https://raw.githubusercontent.com/microsoft/unilm/master/beats"
_BEATS_SOURCE_FILES = {
    "BEATs.py":      f"{_BEATS_BASE_URL}/BEATs.py",
    "Tokenizers.py": f"{_BEATS_BASE_URL}/Tokenizers.py",
    "backbone.py":   f"{_BEATS_BASE_URL}/backbone.py",
    "modules.py":    f"{_BEATS_BASE_URL}/modules.py",
    "quantizer.py":  f"{_BEATS_BASE_URL}/quantizer.py",
}

# Default HuggingFace source for BEATs checkpoint.
# Override via env vars AI_BEATS_HF_REPO_ID / AI_BEATS_HF_FILENAME.
_BEATS_DEFAULT_HF_REPO   = os.environ.get("AI_BEATS_HF_REPO_ID",   "agkphysics/AudioSet-BEATs")
_BEATS_DEFAULT_HF_FILE   = os.environ.get("AI_BEATS_HF_FILENAME",   "BEATs_iter3_plus_AS2M_finetuned_cpt2.pt")
_BEATS_DIRECT_URL        = os.environ.get("AI_BEATS_DOWNLOAD_URL", "")

# Project root = ai_module/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class MissingAsset:
    config_key: str
    expected_path: str
    fix_hint: str


@dataclass
class DoctorReport:
    device_info: DeviceInfo
    gpu_usable: bool
    resolved_paths: Dict[str, str] = field(default_factory=dict)
    auto_fetched: List[str] = field(default_factory=list)
    missing: List[MissingAsset] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.missing) == 0


class Doctor:
    """Static utility — all methods are classmethods."""

    # ==================================================================
    # A) Environment & GPU checks
    # ==================================================================
    @classmethod
    def _check_environment(cls, config: Dict[str, Any]) -> DeviceInfo:
        """Print env details and return DeviceInfo."""
        dev = select_device(config, force_refresh=True)

        logger.info("=" * 60)
        logger.info("STARTUP DOCTOR — Environment")
        logger.info("=" * 60)
        logger.info(f"  torch.__version__          = {dev.torch_version}")
        logger.info(f"  torch.cuda (torch_gpu)     = {dev.torch_gpu}")
        if dev.torch_gpu:
            try:
                import torch
                logger.info(f"  torch.cuda.device_count()  = {torch.cuda.device_count()}")
            except Exception:
                pass
            logger.info(f"  torch.cuda.device_name(0)  = {dev.device_name}")
        logger.info(f"  onnxruntime.__version__     = {dev.ort_version}")
        logger.info(f"  ort.available_providers     = {dev.ort_available_providers}")
        logger.info(f"  ort_cuda                    = {dev.ort_cuda}")
        logger.info(f"  gpu_usable (any backend)    = {dev.gpu_usable}")
        logger.info(f"  selected torch_device       = {dev.torch_device}")
        logger.info(f"  selected ort_providers      = {dev.ort_providers}")
        logger.info("=" * 60)

        return dev

    # ==================================================================
    # B) Path normalization + search
    # ==================================================================
    @classmethod
    def _build_search_dirs(cls, config: Dict[str, Any]) -> List[Path]:
        """Return ordered list of directories to search for assets."""
        dirs: List[Path] = [
            PROJECT_ROOT / "models",
            PROJECT_ROOT / "weights",
            PROJECT_ROOT / "third_party",
            PROJECT_ROOT.parent,   # one level up
        ]
        extra = (
            config.get("runtime", {}).get("search_paths", [])
        )
        for p in extra:
            resolved = Path(p).resolve() if Path(p).is_absolute() else (PROJECT_ROOT / p).resolve()
            if resolved not in dirs:
                dirs.append(resolved)
        return dirs

    @classmethod
    def _resolve_path(cls, raw: str, search_dirs: List[Path],
                      config_key: str, report: DoctorReport) -> Optional[str]:
        """
        Convert *raw* to absolute path.  If the file exists, return it.
        Otherwise search in *search_dirs*.  On success, log & record.
        Returns None if not found anywhere.
        """
        if not raw:
            return None

        # Already absolute?
        p = Path(raw)
        if not p.is_absolute():
            p = (PROJECT_ROOT / raw).resolve()

        if p.exists():
            report.resolved_paths[config_key] = str(p)
            return str(p)

        # Search
        filename = Path(raw).name
        for d in search_dirs:
            candidate = d / filename
            if candidate.exists():
                resolved = str(candidate.resolve())
                logger.info(f"Resolved {config_key} → {resolved}  (found in {d})")
                report.resolved_paths[config_key] = resolved
                return resolved

            # Also try subdirectories one level deep
            if d.is_dir():
                for sub in d.iterdir():
                    if sub.is_dir():
                        candidate = sub / filename
                        if candidate.exists():
                            resolved = str(candidate.resolve())
                            logger.info(f"Resolved {config_key} → {resolved}  (found in {sub})")
                            report.resolved_paths[config_key] = resolved
                            return resolved

        return None

    # ==================================================================
    # C) Deterministic auto-fetch
    # ==================================================================
    @classmethod
    def _try_ultralytics_fetch(cls, weight_name: str, report: DoctorReport) -> Optional[str]:
        """If weight_name is a known Ultralytics pattern, let it auto-download."""
        basename = Path(weight_name).name
        if basename not in _ULTRALYTICS_AUTO:
            return None

        try:
            logger.info(f"Auto-fetching Ultralytics weight: {basename}")
            from ultralytics import YOLO, RTDETR
            if "rtdetr" in basename.lower():
                model = RTDETR(basename)
            else:
                model = YOLO(basename)
            # Ultralytics downloads to its cache; also copy to models/
            models_dir = PROJECT_ROOT / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            dest = models_dir / basename
            if not dest.exists():
                # Find the cached file
                cache_path = Path(model.ckpt_path) if hasattr(model, "ckpt_path") else None
                if cache_path and cache_path.exists():
                    shutil.copy2(str(cache_path), str(dest))
                    logger.info(f"Copied {basename} → {dest}")
            report.auto_fetched.append(basename)
            return str(dest) if dest.exists() else str(cache_path) if cache_path else basename
        except Exception as e:
            logger.warning(f"Ultralytics auto-fetch failed for {basename}: {e}")
            return None

    @classmethod
    def _try_hf_fetch(cls, hf_repo_id: str, hf_filename: str,
                      report: DoctorReport,
                      local_rename: str = "") -> Optional[str]:
        """
        Download from HuggingFace Hub if repo_id provided.

        Args:
            hf_repo_id:   HF repository (e.g. "SHOU-ISD/fire-and-smoke")
            hf_filename:  File to download from the repo (e.g. "yolov8n.pt")
            report:       DoctorReport to append auto-fetched entries
            local_rename: If provided, copy/rename the downloaded file to
                          ``models/<local_rename>`` so the rest of the
                          codebase can reference a deterministic filename.
        """
        if not hf_repo_id:
            return None
        try:
            from huggingface_hub import hf_hub_download  # type: ignore
            logger.info(f"Auto-fetching from HuggingFace: {hf_repo_id}/{hf_filename}")
            local = hf_hub_download(repo_id=hf_repo_id, filename=hf_filename)
            # Copy to models/ (with optional rename)
            models_dir = PROJECT_ROOT / "models"
            final_name = local_rename if local_rename else hf_filename
            dest = Path(final_name)
            if not dest.is_absolute():
                dest = models_dir / dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(local, str(dest))
            tag = f"hf:{hf_repo_id}/{hf_filename}"
            if local_rename:
                tag += f" → {final_name}"
            report.auto_fetched.append(tag)
            logger.info(f"Auto-fetched {hf_repo_id}/{hf_filename} from HF → {dest}")
            return str(dest)
        except ImportError:
            logger.warning("huggingface_hub not installed — skipping HF auto-fetch")
            return None
        except Exception as e:
            logger.warning(f"HuggingFace fetch failed ({hf_repo_id}/{hf_filename}): {e}")
            return None

    @classmethod
    def _try_rtdetrv2_fetch(cls, weight_name: str, report: DoctorReport) -> Optional[str]:
        """Auto-download official RTDETRv2 weights from GitHub releases."""
        basename = Path(weight_name).name
        url = _RTDETRV2_WEIGHTS.get(basename)
        if not url:
            return None

        models_dir = PROJECT_ROOT / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        dest = models_dir / basename

        if dest.exists():
            report.resolved_paths[f"rtdetrv2:{basename}"] = str(dest)
            return str(dest)

        try:
            import urllib.request
            logger.info(f"Downloading RTDETRv2 weights: {basename} from GitHub …")
            urllib.request.urlretrieve(url, str(dest))
            report.auto_fetched.append(f"rtdetrv2:{basename}")
            logger.info(f"RTDETRv2 weights downloaded → {dest}")
            return str(dest)
        except Exception as e:
            logger.warning(f"RTDETRv2 auto-download failed for {basename}: {e}")
            if dest.exists():
                dest.unlink()  # remove partial download
            return None

    @classmethod
    def _try_git_clone(cls, repo_url: str, repo_dir: str,
                       report: DoctorReport) -> Optional[str]:
        """Shallow-clone a git repo if repo_url is provided."""
        if not repo_url:
            return None
        dest = (PROJECT_ROOT / repo_dir).resolve() if not Path(repo_dir).is_absolute() else Path(repo_dir)
        if dest.exists() and any(dest.iterdir()):
            logger.info(f"Repo already exists: {dest}")
            report.resolved_paths[f"clone:{repo_url}"] = str(dest)
            return str(dest)

        try:
            logger.info(f"Cloning {repo_url} → {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(dest)],
                check=True, capture_output=True, text=True, timeout=120,
            )
            report.auto_fetched.append(f"git:{repo_url}")
            logger.info(f"Clone OK: {dest}")
            return str(dest)
        except FileNotFoundError:
            logger.warning("git not found on PATH — cannot auto-clone")
            return None
        except subprocess.CalledProcessError as e:
            logger.warning(f"git clone failed: {e.stderr.strip()}")
            return None
        except Exception as e:
            logger.warning(f"git clone error: {e}")
            return None

    # ==================================================================
    # D-ext) BEATs asset provisioning
    # ==================================================================
    @classmethod
    def _ensure_beats_assets(cls, audio_cfg: Dict[str, Any], report: DoctorReport) -> Optional[str]:
        """
        Ensure BEATs.py/Tokenizers.py source files and the checkpoint are
        present.  Downloads them automatically on first run.

        Returns the resolved checkpoint path (str) or None if unavailable.
        """
        beats_src_dir = PROJECT_ROOT / "third_party" / "beats"

        # ── 1. Source files (BEATs.py + Tokenizers.py) ───────────────
        missing_src = [
            fname for fname in ("BEATs.py", "Tokenizers.py")
            if not (beats_src_dir / fname).exists()
        ]
        if missing_src:
            logger.info("BEATs source files missing — downloading from GitHub …")
            beats_src_dir.mkdir(parents=True, exist_ok=True)
            for fname in missing_src:
                url = _BEATS_SOURCE_FILES[fname]
                dest = beats_src_dir / fname
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "VigilZone/1.0"}
                    )
                    with urllib.request.urlopen(req, timeout=60) as resp, \
                            open(dest, "wb") as fh:
                        fh.write(resp.read())
                    logger.info(f"Downloaded BEATs source: {fname} → {dest}")
                    report.auto_fetched.append(f"beats-src:{fname}")
                except Exception as exc:
                    logger.warning(f"Failed to download BEATs source {fname}: {exc}")
                    report.warnings.append(
                        f"BEATs source {fname} unavailable; audio lane will be disabled."
                    )

        # ── 2. Checkpoint file ────────────────────────────────────────
        raw_model_path = os.getenv("AI_BEATS_MODEL_PATH") or audio_cfg.get("model_path", "")
        if not raw_model_path:
            raw_model_path = (
                f"models/audio/beats/{_BEATS_DEFAULT_HF_FILE}"
            )

        checkpoint_path = Path(raw_model_path)
        if not checkpoint_path.is_absolute():
            checkpoint_path = PROJECT_ROOT / raw_model_path
        checkpoint_path = checkpoint_path.resolve()

        if checkpoint_path.exists():
            logger.info(f"BEATs checkpoint found: {checkpoint_path}")
            audio_cfg["model_path"] = str(checkpoint_path)
            report.resolved_paths["models.audio_anomaly.model_path"] = str(checkpoint_path)
            return str(checkpoint_path)

        # Not present — try to download
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("BEATs checkpoint not found — attempting auto-download …")

        # Strategy A: direct URL from env var
        if _BEATS_DIRECT_URL:
            logger.info(f"Using AI_BEATS_DOWNLOAD_URL: {_BEATS_DIRECT_URL}")
            fetched = None
            try:
                import urllib.request
                tmp = checkpoint_path.with_suffix(".tmp")
                req = urllib.request.Request(
                    _BEATS_DIRECT_URL, headers={"User-Agent": "VigilZone/1.0"}
                )
                with urllib.request.urlopen(req, timeout=600) as resp, \
                        open(tmp, "wb") as fh:
                    fh.write(resp.read())
                tmp.rename(checkpoint_path)
                fetched = str(checkpoint_path)
                logger.info(f"BEATs checkpoint downloaded via direct URL → {checkpoint_path}")
                report.auto_fetched.append(f"beats-checkpoint:{checkpoint_path.name}")
            except Exception as exc:
                logger.warning(f"Direct URL download failed: {exc}")
                if tmp.exists():
                    tmp.unlink()
            if fetched:
                audio_cfg["model_path"] = fetched
                report.resolved_paths["models.audio_anomaly.model_path"] = fetched
                return fetched

        # Strategy B: HuggingFace Hub (uses existing _try_hf_fetch)
        hf_repo = audio_cfg.get("hf_repo_id", _BEATS_DEFAULT_HF_REPO)
        hf_file = audio_cfg.get("hf_filename",  _BEATS_DEFAULT_HF_FILE)
        models_dir = (PROJECT_ROOT / "models").resolve()
        try:
            local_rename = str(checkpoint_path.relative_to(models_dir))
        except ValueError:
            local_rename = str(checkpoint_path)
        fetched = cls._try_hf_fetch(
            hf_repo, hf_file, report,
            local_rename=local_rename,
        )
        if fetched:
            audio_cfg["model_path"] = fetched
            report.resolved_paths["models.audio_anomaly.model_path"] = fetched
            return fetched

        # All strategies exhausted
        logger.warning(
            "BEATs checkpoint could not be downloaded automatically. "
            "The audio_anomaly lane will be disabled at runtime. "
            "To fix: set AI_BEATS_DOWNLOAD_URL or run: "
            "python scripts/download_beats.py"
        )
        report.warnings.append(
            "BEATs checkpoint unavailable — audio_anomaly lane disabled."
        )
        return None

    # ==================================================================
    # D) Walk config and resolve each model entry
    # ==================================================================
    @classmethod
    def _resolve_models(cls, config: Dict[str, Any], report: DoctorReport):
        """Walk models config, resolve paths, attempt auto-fetch, collect missing."""
        search_dirs = cls._build_search_dirs(config)
        models = config.get("models", {})

        # ── Map of config_key → (path_field, is_required) ──
        path_entries = [
            ("models.rt_detr", "native_weights", False),   # RTDETRv2 .pth (new primary)
            ("models.rt_detr", "weights", False),           # legacy .pt fallback
            ("models.yolov8", "weights", False),
            ("models.fire_smoke", "weights", False),
            ("models.weapon_yolo", "weights", False),
            ("models.anomalyclip", "model_path", False),
            ("models.temporal_verifier", "model_path", False),
            ("models.person_detector", "weights", False),
        ]

        for config_key, path_field, required in path_entries:
            section_key = config_key.split(".")[-1]
            section = models.get(section_key, {})
            if not section:
                continue

            # Skip disabled lanes
            if not section.get("enabled", True):
                continue

            # ── Temporal verifier: TorchHub source skips file resolution ──
            if section_key == "temporal_verifier" and section.get("source") == "torchhub":
                continue  # no local file needed, loaded via torch.hub

            raw_path = section.get(path_field, "")
            if not raw_path:
                continue

            # 1. Try resolve/search
            resolved = cls._resolve_path(raw_path, search_dirs, config_key, report)
            if resolved:
                section[path_field] = resolved
                continue

            # 2. Try Ultralytics auto-download
            fetched = cls._try_ultralytics_fetch(raw_path, report)
            if fetched:
                section[path_field] = fetched
                continue

            # 2b. Try RTDETRv2 auto-download from GitHub releases
            fetched = cls._try_rtdetrv2_fetch(raw_path, report)
            if fetched:
                section[path_field] = fetched
                continue

            # 3. Try HuggingFace (with rename: hf_filename → weights basename)
            hf_repo = section.get("hf_repo_id", "")
            hf_file = section.get("hf_filename", "")
            if hf_repo and hf_file:
                # local_rename: use the configured weights filename so the
                # rest of the code can reference a deterministic path.
                local_rename = Path(raw_path).name
                fetched = cls._try_hf_fetch(
                    hf_repo, hf_file, report, local_rename=local_rename,
                )
                if fetched:
                    section[path_field] = fetched
                    continue

            # 4. Still missing — record with an actionable hint
            if section_key == "anomalyclip":
                # AnomalyCLIP has no known public checkpoint — stub is
                # the expected fallback.  Don't treat as missing asset;
                # just log at INFO level so the warning block stays clean.
                logger.info(
                    "AnomalyCLIP: no checkpoint configured — "
                    "using motion-energy stub (expected default). "
                    f"Set {config_key}.hf_repo_id + hf_filename to "
                    "enable the full model."
                )
                continue
            else:
                fix_hint = (
                    f"Place file at {PROJECT_ROOT / raw_path}  OR  "
                    f"set {config_key}.hf_repo_id + hf_filename in config"
                )
            report.missing.append(MissingAsset(config_key, raw_path, fix_hint))

        # ── AnyAnomaly repo_dir ──
        aa = models.get("anyanomaly", {})
        if aa.get("enabled", True):
            repo_dir = aa.get("repo_dir", "third_party/Paper-AnyAnomaly")
            repo_dir_abs = (
                Path(repo_dir).resolve()
                if Path(repo_dir).is_absolute()
                else (PROJECT_ROOT / repo_dir).resolve()
            )
            if not repo_dir_abs.exists():
                repo_url = aa.get("repo_url", "")
                if repo_url:
                    result = cls._try_git_clone(repo_url, repo_dir, report)
                    if result:
                        aa["repo_dir"] = result
                    else:
                        report.missing.append(MissingAsset(
                            "models.anyanomaly.repo_dir",
                            repo_dir,
                            f"Clone manually: git clone {repo_url} {repo_dir}",
                        ))
                else:
                    report.missing.append(MissingAsset(
                        "models.anyanomaly.repo_dir",
                        repo_dir,
                        "Set models.anyanomaly.repo_url for auto-clone OR "
                        "clone manually into third_party/Paper-AnyAnomaly",
                    ))
            else:
                aa["repo_dir"] = str(repo_dir_abs)

        # ── BEATs (audio_anomaly lane) ───────────────────────────────────
        audio_cfg = models.get("audio_anomaly", {})
        if audio_cfg.get("enabled", False):
            cls._ensure_beats_assets(audio_cfg, report)

    # ==================================================================
    # PUBLIC: run_all
    # ==================================================================
    @classmethod
    def run_all(cls, config: Dict[str, Any]) -> DoctorReport:
        """
        Run all startup checks.  Call BEFORE any lane / model initialisation.

        Returns DoctorReport.  If ``report.ok`` is False, the caller should
        log the missing-asset table and decide whether to abort or continue
        with degraded functionality.
        """
        # A) Environment
        dev = cls._check_environment(config)
        report = DoctorReport(device_info=dev, gpu_usable=dev.gpu_usable)

        # B + C) Resolve paths + auto-fetch
        cls._resolve_models(config, report)

        # D) Consolidated report
        if report.missing:
            logger.warning("=" * 60)
            logger.warning(f"Missing assets ({len(report.missing)}):")
            logger.warning("=" * 60)
            for m in report.missing:
                logger.warning(f"  - {m.config_key}: {m.expected_path}")
                logger.warning(f"    Fix: {m.fix_hint}")
            logger.warning("=" * 60)
            logger.warning(
                "Lanes with missing assets will be DISABLED. "
                "Provide the files or configure deterministic sources to enable them."
            )
        else:
            logger.info("All assets resolved — no missing files.")

        if report.auto_fetched:
            logger.info(f"Auto-fetched: {report.auto_fetched}")

        return report
