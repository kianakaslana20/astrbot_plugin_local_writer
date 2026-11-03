# -*- coding: utf-8 -*-
"""一键下载写作模型（走 hf-mirror，16 线程分段）。

用法:
    python download-models.py 7b
    python download-models.py 14b
    python download-models.py all
"""
import os
import subprocess
import sys

MODELS = {
    "7b": (
        "https://hf-mirror.com/mradermacher/Qwen2.5-7B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf",
        "Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf",
    ),
    "14b": (
        "https://hf-mirror.com/mradermacher/Qwen2.5-14B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf",
        "Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf",
    ),
}

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("MODEL_DIR", r"E:\AI-NSFW-Writer\models")


def fetch(key: str) -> None:
    url, name = MODELS[key]
    out = os.path.join(OUT_DIR, name)
    if os.path.exists(out) and os.path.getsize(out) > 1 << 30:
        print(f"[skip] {name} 已存在 ({os.path.getsize(out)/2**30:.2f} GB)")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[get ] {name}")
    subprocess.check_call(
        [sys.executable, os.path.join(HERE, "pdl.py"), url, out, "16", "8"]
    )


if __name__ == "__main__":
    target = (sys.argv[1] if len(sys.argv) > 1 else "7b").lower()
    keys = list(MODELS) if target == "all" else [target]
    for k in keys:
        if k not in MODELS:
            sys.exit(f"未知模型: {k}（可选 7b / 14b / all）")
        fetch(k)
    print("done ->", OUT_DIR)
