# -*- coding: utf-8 -*-
"""把 release 里下载的分卷合并成完整 gguf，并校验大小。

用法:
    python join-models.py Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf
    （分卷放在同一目录，命名 xxx.gguf.part01 / part02 ...）
"""
import glob
import os
import sys

EXPECTED = {
    "Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf": 4684333312,
    "Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf": 8988111264,
}


def main(target: str) -> None:
    folder = os.path.dirname(os.path.abspath(target)) or "."
    base = os.path.basename(target)
    parts = sorted(glob.glob(os.path.join(folder, base + ".part*")))
    if not parts:
        sys.exit(f"没找到分卷：{base}.part*")
    print(f"找到 {len(parts)} 个分卷")
    tmp = target + ".joining"
    written = 0
    with open(tmp, "wb") as out:
        for p in parts:
            n = 0
            with open(p, "rb") as f:
                while True:
                    buf = f.read(8 * 1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)
                    n += len(buf)
            written += n
            print(f"  + {os.path.basename(p)}  {n/2**30:.2f} GB")
    os.replace(tmp, target)
    size = os.path.getsize(target)
    print(f"合并完成 {size} 字节 ({size/2**30:.2f} GB)")
    exp = EXPECTED.get(base)
    if exp and size != exp:
        print(f"[!] 大小不符，期望 {exp}")
        sys.exit(1)
    print("校验通过")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
