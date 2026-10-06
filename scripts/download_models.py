# -*- coding: utf-8 -*-
"""download_models.py — fetch local embedding models for semantic search (a43).

Why
---
`pip install "memtether[vector]"` installs chromadb/onnxruntime but NOT the
~22-560 MB ONNX weights (models/ is git-ignored — binaries never ship in the
wheel). Before a43 there was no documented way to get them: semantic search
silently degraded to keyword for every pip user. This script closes that gap
with a stdlib-only downloader (no huggingface_hub dependency).

Usage
-----
    python scripts/download_models.py --profile bge-small-zh   # 22 MB, fast
    python scripts/download_models.py --profile bge-m3-int8    # 560 MB, best
    python scripts/download_models.py --list

Defaults to the repo's models/ dir; override with MEM_MODELS_DIR or --out.
Downloads try huggingface.co first and fall back to hf-mirror.com (both
probed 2026-10-06). Partial downloads resume; files are size-verified.
"""
import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODELS_DIR = os.environ.get("MEM_MODELS_DIR") or os.path.join(ROOT, "models")

# repo id on the HF Hub, and the files each profile needs (relative paths)
# target_dir matches embed_local.MODELS[...]['dir'] so the downloaded files
# land exactly where embed_local._profile() looks for them.
PROFILES = {
    "bge-small-zh": dict(
        repo="Xenova/bge-small-zh-v1.5",
        target_dir="bge_small_zh",
        onnx="onnx/model_fp16.onnx", dim=512,
        files=["config.json", "tokenizer.json", "tokenizer_config.json",
               "special_tokens_map.json", "onnx/model_fp16.onnx"],
        note="~46 MB total; fastest cold start; ~88.7% on the hard bench",
    ),
    "bge-m3-int8": dict(
        repo="Xenova/bge-m3",
        target_dir="bge_m3",
        onnx="onnx/model_quantized.onnx", dim=1024,
        files=["config.json", "tokenizer.json", "tokenizer_config.json",
               "special_tokens_map.json", "sentencepiece.bpe.model",
               "onnx/model_quantized.onnx"],
        note="560 MB total; default profile; 93.5% on the hard bench",
    ),
}
DEFAULT_PROFILE = "bge-m3-int8"
MIRRORS = ["https://huggingface.co", "https://hf-mirror.com"]


def _fetch(url, dest, min_size=1):
    """Stream url to dest with resume; returns True if the file is complete."""
    tmp = dest + ".part"
    done = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    req = urllib.request.Request(url, headers={"User-Agent": "memtether-downloader"})
    if done:
        req.add_header("Range", f"bytes={done}-")
    try:
        r = urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        # 416 = range not satisfiable → file already complete upstream
        if hasattr(e, "code") and e.code == 416 and os.path.exists(dest):
            return True
        raise
    total = r.headers.get("Content-Length")
    mode = "ab" if done else "wb"
    with open(tmp, mode) as f, r:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            if total:
                pct = min(100, 100 * (done + f.tell()) // int(total))
                sys.stdout.write(f"\r    {pct:3d}%  {dest_name(dest)}")
                sys.stdout.flush()
    sys.stdout.write("\n")
    if os.path.getsize(tmp) >= min_size:
        os.replace(tmp, dest)
        return True
    return False


def dest_name(p):
    return os.path.basename(p) if os.sep not in p else os.path.join(*p.split(os.sep)[-2:])


def download(profile_key, out_dir=None):
    prof = PROFILES[profile_key]
    out = out_dir or MODELS_DIR
    base = os.path.join(out, prof["target_dir"])
    print(f"[{profile_key}] {prof['note']}")
    for rel in prof["files"]:
        dest = os.path.join(base, rel)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            print(f"  ✓ cached  {rel}")
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        ok = False
        for host in MIRRORS:
            url = f"{host}/{prof['repo']}/resolve/main/{rel}"
            try:
                print(f"  ↓ {rel}  ({host.split('//')[1]})")
                if _fetch(url, dest):
                    ok = True
                    break
            except Exception as e:
                print(f"    mirror failed: {e}")
        if not ok:
            # leave the .part for resume, but report failure
            print(f"  ✗ could not download {rel}")
            return False
    onnx_ok = os.path.exists(os.path.join(base, prof["onnx"]))
    print(f"[{profile_key}] {'complete' if onnx_ok else 'INCOMPLETE'} → {base}")
    return onnx_ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE)
    ap.add_argument("--out", default=None, help="target models dir (default models/ or MEM_MODELS_DIR)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for k, v in PROFILES.items():
            print(f"{k:14s} {v['note']}")
        return 0
    ok = download(a.profile, a.out)
    if ok:
        print("\nNext steps:")
        print("  pip install \"memtether[vector]\"   # if not installed yet")
        print("  python -m memsearch --rebuild      # build the vector index")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
