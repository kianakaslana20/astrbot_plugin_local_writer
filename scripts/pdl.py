#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多线程分段下载器：Range 分块 + 每段独立重试 + 大小校验 + 合并。
用法: python pdl.py <url> <out> [threads] [chunk_mb]
"""
import os, sys, time, shutil, threading, urllib.request, urllib.error

url = sys.argv[1]
out = sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 16
chunk = (int(sys.argv[4]) if len(sys.argv) > 4 else 8) * 1024 * 1024

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def head_size(u):
    req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length", 0))


def fetch_range(u, start, end, path, retries=8):
    want = end - start + 1
    for attempt in range(retries):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if have >= want:
            return True
        s = start + have
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA,
                "Range": "bytes=%d-%d" % (s, end),
            })
            with urllib.request.urlopen(req, timeout=120) as r:
                if r.status not in (200, 206):
                    raise IOError("status %s" % r.status)
                with open(path, "ab") as f:
                    while True:
                        b = r.read(262144)
                        if not b:
                            break
                        f.write(b)
        except Exception as e:
            sys.stderr.write("retry %d/%d %s: %s\n" % (attempt + 1, retries, os.path.basename(path), e))
            time.sleep(2 + attempt * 2)
            continue
    return os.path.exists(path) and os.path.getsize(path) >= want


def main():
    total = head_size(url)
    if total <= 0:
        print("no content-length", file=sys.stderr)
        return 1
    print("total bytes: %d (%.1f MB)" % (total, total / 1048576.0), flush=True)

    tmp = out + ".parts"
    os.makedirs(tmp, exist_ok=True)
    ranges = []
    i = 0
    pos = 0
    while pos < total:
        end = min(pos + chunk - 1, total - 1)
        ranges.append((i, pos, end, os.path.join(tmp, "p%05d" % i)))
        pos = end + 1
        i += 1

    done = [False] * len(ranges)
    lock = threading.Lock()
    fail = []

    def worker(slot):
        idx = slot
        while idx < len(ranges):
            n, s, e, p = ranges[idx]
            ok = fetch_range(url, s, e, p)
            with lock:
                done[idx] = ok
                if not ok:
                    fail.append(n)
            idx += threads

    workers = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(min(threads, len(ranges)))]
    for w in workers:
        w.start()

    last = -1
    while any(w.is_alive() for w in workers):
        time.sleep(5)
        got = sum((os.path.getsize(p) if os.path.exists(p) else 0) for _, _, _, p in ranges)
        if got != last:
            last = got
            print("progress %.1f%%  %.1f/%.1f MB" % (got * 100.0 / total, got / 1048576.0, total / 1048576.0), flush=True)
    for w in workers:
        w.join(timeout=5)

    if fail:
        print("FAILED parts: %s" % fail, file=sys.stderr)
        return 2

    print("merging...", flush=True)
    if os.path.exists(out):
        os.remove(out)
    with open(out, "wb") as w:
        for _, _, _, p in ranges:
            with open(p, "rb") as r:
                shutil.copyfileobj(r, w, 8 * 1048576)
    size = os.path.getsize(out)
    print("done: %s  %d bytes (%.2f MB)" % (out, size, size / 1048576.0), flush=True)
    if size != total:
        print("SIZE MISMATCH", file=sys.stderr)
        return 3
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())