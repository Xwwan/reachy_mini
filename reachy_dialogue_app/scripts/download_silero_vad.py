#!/usr/bin/env python3
"""下载并校验自动语音使用的默认 Silero VAD ONNX 模型。"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path


DEFAULT_URL = (
    "https://huggingface.co/bitsydarel/silero-vad-onnx/resolve/main/"
    "silero_vad_v6.2.1.onnx"
)
DEFAULT_SHA256 = (
    "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "models" / "silero_vad.onnx"


def sha256_file(path: Path) -> str:
    """计算文件 SHA256，用于确认模型下载完整。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """脚本入口：如果模型不存在或校验失败就重新下载。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sha256", default=DEFAULT_SHA256)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.force:
        print(f"Model already exists: {output}")
        print(f"sha256={sha256_file(output)}")
        return

    tmp_path = output.with_suffix(output.suffix + ".tmp")
    print(f"Downloading {args.url}")
    urllib.request.urlretrieve(args.url, tmp_path)
    digest = sha256_file(tmp_path)
    if args.sha256 and digest.lower() != args.sha256.lower():
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(
            f"SHA256 mismatch for {tmp_path}: expected {args.sha256}, got {digest}"
        )
    tmp_path.replace(output)
    print(f"Saved {output}")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
