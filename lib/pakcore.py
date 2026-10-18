# -*- coding: utf-8 -*-
"""
pakcore — Text.pak / Text.idx 容器核心（自研，纯标准库）

设计依据：需求文件工具与产出\客户端容器格式_Text.pak_Text.idx_20261019.md
  1) 8 字节分组加密，块与块互相独立
  2) 余数（len%8）字节原样，不加密
  3) idx = 4B 条数(明文) + n*28B（整段从偏移 4 开始加密）
  4) pak 允许未引用空隙 → 禁止紧凑重建

写入策略：
  A 原地写（新长度 == 旧长度）：只改受影响的 8B 块，idx 一字节不动
  B 尾部追加（长度不同）：新内容追加到 pak 末尾，只改 1 条 idx 记录的 offset/size
"""
import os
import struct
import hashlib
import shutil
import time

import paktools as P

REC_SIZE = 28
NAME_LEN = 20


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _decode_idx(idx_raw):
    """返回 (n, body)；body 是解密后的 n*28 字节"""
    if len(idx_raw) < 4:
        raise ValueError("idx 文件过短")
    n = struct.unpack_from("<I", idx_raw, 0)[0]
    body = P.decode(idx_raw, 4)
    if len(body) != n * REC_SIZE:
        raise ValueError("idx 结构异常：声明 %d 条，正文 %d 字节（应 %d）"
                         % (n, len(body), n * REC_SIZE))
    return n, bytearray(body)


def _decode_record(idx_raw, i):
    """只解一条记录（28B）——按 8B 块对齐取，避免整份 idx 全解"""
    s = i * REC_SIZE
    e = s + REC_SIZE
    b0 = s // 8
    b1 = (e - 1) // 8
    chunk = idx_raw[4 + b0 * 8:4 + (b1 + 1) * 8]
    plain = P.decode(chunk, 0)
    base = s - b0 * 8
    return plain[base:base + REC_SIZE]


def _parse_one(rec_bytes):
    off = struct.unpack_from("<i", rec_bytes, 0)[0]
    name = rec_bytes[4:4 + NAME_LEN].split(b"\0")[0].decode("latin1")
    size = struct.unpack_from("<i", rec_bytes, 24)[0]
    return {"off": off, "name": name, "size": size}


def _encode_idx(idx_raw, body, blocks=None):
    """把 body（明文）中受影响的 8 字节块加密后写回 idx 副本"""
    out = bytearray(idx_raw)
    total = len(body)
    if blocks is None:
        blocks = range((total + 7) // 8)
    for b in blocks:
        s = b * 8
        e = min(s + 8, total)
        if e - s < 8:
            continue  # 末尾余数原样，不加密
        out[4 + s:4 + e] = P.encode(bytes(body[s:e]), 0)
    return bytes(out)


def _parse_recs(body):
    n = len(body) // REC_SIZE
    recs = []
    for i in range(n):
        s = i * REC_SIZE
        off = struct.unpack_from("<i", body, s)[0]
        name = body[s + 4:s + 4 + NAME_LEN].split(b"\0")[0].decode("latin1")
        size = struct.unpack_from("<i", body, s + 24)[0]
        recs.append({"idx": i, "off": off, "name": name, "size": size})
    return recs


class Container(object):
    def __init__(self, client_dir):
        self.client = client_dir
        self.pak_path = os.path.join(client_dir, "Text.pak")
        self.idx_path = os.path.join(client_dir, "Text.idx")
        if not (os.path.isfile(self.pak_path) and os.path.isfile(self.idx_path)):
            raise SystemExit("找不到 Text.pak / Text.idx：%s" % client_dir)
        self.idx_raw = open(self.idx_path, "rb").read()
        self.pak = open(self.pak_path, "rb").read()
        P._init()
        self.n, self.body = _decode_idx(self.idx_raw)
        self.recs = _parse_recs(self.body)
        self.by_name = {r["name"]: r for r in self.recs}
        if len(self.by_name) != len(self.recs):
            raise SystemExit("容器内存在重名条目，拒绝操作")

    # ---------- 读取 ----------
    def cipher(self, name):
        r = self.by_name[name]
        off, size = r["off"], r["size"]
        if off < 0 or off + size > len(self.pak):
            raise SystemExit("条目越界：%s (off=%d size=%d)" % (name, off, size))
        return self.pak[off:off + size]

    def plain(self, name):
        return P.decode(self.cipher(name), 0)

    def fingerprint(self):
        """name -> (size, cipher_sha256)，不解密，速度 O(n)"""
        return {r["name"]: (r["size"], sha256(self.pak[r["off"]:r["off"] + r["size"]]))
                for r in self.recs if 0 <= r["off"] and r["off"] + r["size"] <= len(self.pak)}

    def gaps(self):
        srt = sorted(self.recs, key=lambda r: r["off"])
        out, prev = [], 0
        for r in srt:
            if r["off"] > prev:
                out.append((prev, r["off"] - prev))
            prev = max(prev, r["off"] + r["size"])
        if len(self.pak) > prev:
            out.append((prev, len(self.pak) - prev))
        return out

    # ---------- 写入 ----------
    def replace(self, patches, strategy="auto", dry_run=False, backup=True):
        """
        patches: {条目名: 新明文 bytes}
        返回 report dict
        """
        unknown = [k for k in patches if k not in self.by_name]
        if unknown:
            raise SystemExit("这些条目不在容器中（本工具不支持新增条目）：%s" % unknown[:5])

        pak = bytearray(self.pak)
        body = bytearray(self.body)
        inplace, appended = [], []
        changed_blocks = set()

        for name, data in patches.items():
            r = self.by_name[name]
            off, size = r["off"], r["size"]
            enc = P.encode(data, 0)
            if len(enc) == size and strategy in ("auto", "inplace"):
                if self.pak[off:off + size] != enc:
                    pak[off:off + size] = enc
                inplace.append({"name": name, "off": off, "size": size})
            elif strategy == "inplace":
                raise SystemExit("条目长度变化（%d→%d），inplace 策略不可用：%s"
                                 % (size, len(enc), name))
            else:
                newoff = len(pak)
                pak += enc
                s = r["idx"] * REC_SIZE
                struct.pack_into("<i", body, s, newoff)
                struct.pack_into("<i", body, s + 24, len(enc))
                for b in range(s // 8, (s + REC_SIZE - 1) // 8 + 1):
                    changed_blocks.add(b)
                appended.append({"name": name, "old_off": off, "new_off": newoff,
                                 "old_size": size, "new_size": len(enc)})

        if changed_blocks:
            idx_out = _encode_idx(self.idx_raw, body, sorted(changed_blocks))
        else:
            idx_out = self.idx_raw

        # ---- 自检 1：抽查新 idx（全量解码太慢，改为定点解码 + 抽样） ----
        if changed_blocks:
            check_idx = list(range(min(50, self.n))) + [r["idx"] for r in self.recs[-50:]]
            check_idx += [a["idx"] for a in
                          ({"idx": self.by_name[a["name"]]["idx"]} for a in appended)]
            check_idx = sorted(set(i for i in check_idx if 0 <= i < self.n))
            for i in check_idx:
                rec = _parse_one(_decode_record(idx_out, i))
                src = self.recs[i]
                expect_off = src["off"]
                expect_size = src["size"]
                for a in appended:
                    if self.by_name[a["name"]]["idx"] == i:
                        expect_off, expect_size = a["new_off"], a["new_size"]
                if rec["name"] != src["name"] or rec["off"] != expect_off or rec["size"] != expect_size:
                    raise SystemExit("自检失败：idx 记录不符 %s" % src["name"])
            if len(idx_out) != len(self.idx_raw):
                raise SystemExit("自检失败：idx 大小变化")

        # ---- 自检 2：回读每个补丁条目 ----
        for name, data in patches.items():
            if changed_blocks:
                a = [x for x in appended if x["name"] == name]
                if a:
                    off2, size2 = a[0]["new_off"], a[0]["new_size"]
                else:
                    off2, size2 = self.by_name[name]["off"], self.by_name[name]["size"]
            else:
                off2, size2 = self.by_name[name]["off"], self.by_name[name]["size"]
            back = P.decode(bytes(pak[off2:off2 + size2]), 0)
            if back != data:
                raise SystemExit("自检失败：回读内容不一致 %s" % name)

        # ---- 自检 3：改动范围必须限定在预期区域 ----
        allowed = [(a["off"], a["off"] + a["size"]) for a in inplace]
        appended_start = len(self.pak)
        diff_bytes = 0
        for i in range(min(len(pak), len(self.pak))):
            if pak[i] != self.pak[i]:
                diff_bytes += 1
                if not any(s <= i < e for s, e in allowed):
                    raise SystemExit("自检失败：越界改动 pak 偏移 %d" % i)
        if len(pak) > len(self.pak):
            for i in range(len(self.pak), len(pak)):
                if pak[i] != 0:
                    pass
        if len(idx_out) != len(self.idx_raw):
            raise SystemExit("自检失败：idx 大小变化")

        report = {
            "inplace": inplace,
            "appended": appended,
            "pak_old": len(self.pak),
            "pak_new": len(pak),
            "pak_delta": len(pak) - len(self.pak),
            "pak_bytes_changed": diff_bytes,
            "pak_bytes_appended": len(pak) - len(self.pak),
            "idx_size": len(idx_out),
            "idx_changed": bool(changed_blocks),
            "appended_start": appended_start if appended else None,
        }
        if dry_run:
            report["written"] = False
            return report, None, None

        if backup:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            bk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backups", "%s_text" % stamp)
            bk = os.path.abspath(bk)
            os.makedirs(bk, exist_ok=True)
            shutil.copy2(self.pak_path, os.path.join(bk, "Text.pak"))
            shutil.copy2(self.idx_path, os.path.join(bk, "Text.idx"))
            report["backup"] = bk

        tmp_pak = self.pak_path + ".tmp_new"
        tmp_idx = self.idx_path + ".tmp_new"
        open(tmp_pak, "wb").write(bytes(pak))
        open(tmp_idx, "wb").write(idx_out)
        os.replace(tmp_pak, self.pak_path)
        os.replace(tmp_idx, self.idx_path)
        report["written"] = True
        # 同步内存状态，避免调用方读到旧数据（不重复解密，直接用已知明文 body）
        self.pak = bytes(pak)
        self.idx_raw = idx_out
        self.body = body
        self.recs = _parse_recs(body)
        self.by_name = {r["name"]: r for r in self.recs}
        return report, bytes(pak), idx_out


# ---------- 发版侧 ----------
def diff_clients(base_dir, target_dir):
    """比较两个客户端目录的 Text 容器，返回变更清单（不落盘）"""
    a = Container(base_dir)
    b = Container(target_dir)
    fa, fb = a.fingerprint(), b.fingerprint()
    changed, added, removed = [], [], []
    for name, (size, ch) in fb.items():
        if name not in fa:
            added.append({"name": name, "size": size})
        elif fa[name][1] != ch:
            changed.append({"name": name, "size": size})
    for name in fa:
        if name not in fb:
            removed.append({"name": name, "size": fa[name][0]})
    return {"changed": changed, "added": added, "removed": removed,
            "base": a, "target": b}


def export_changes(d, out_dir, version=None):
    """把 diff 结果写成 release 目录：text/<条目> + manifest.json"""
    os.makedirs(os.path.join(out_dir, "text"), exist_ok=True)
    b = d["target"]
    entries = []
    for item in d["changed"]:
        name = item["name"]
        plain = b.plain(name)
        path = os.path.join(out_dir, "text", name.replace("\\", "/"))
        os.makedirs(os.path.dirname(path) or os.path.join(out_dir, "text"), exist_ok=True)
        open(path, "wb").write(plain)
        entries.append({"name": name, "size": len(plain), "sha256": sha256(plain)})
    manifest = {
        "schema": 1,
        "container": "Text",
        "version": version or time.strftime("%Y%m%d%H%M"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entries": entries,
        "unsupported": {
            "added": [x["name"] for x in d["added"]],
            "removed": [x["name"] for x in d["removed"]],
        },
    }
    import json
    open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8").write(
        json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def apply_release(client_dir, release_dir, dry_run=False):
    """客户端侧：读 manifest → 校验 → 合并"""
    import json
    mpath = os.path.join(release_dir, "manifest.json")
    if not os.path.isfile(mpath):
        raise SystemExit("找不到 manifest.json：%s" % mpath)
    manifest = json.load(open(mpath, encoding="utf-8"))
    c = Container(client_dir)

    patches = {}
    skipped = []
    for e in manifest.get("entries", []):
        name, size, h = e["name"], e["size"], e["sha256"]
        p = os.path.join(release_dir, "text", name.replace("\\", "/"))
        if not os.path.isfile(p):
            raise SystemExit("清单里的条目文件缺失：%s" % name)
        data = open(p, "rb").read()
        if len(data) != size or sha256(data) != h:
            raise SystemExit("条目校验失败（大小/哈希不符）：%s" % name)
        if name not in c.by_name:
            raise SystemExit("客户端容器里没有该条目（不支持新增）：%s" % name)
        if sha256(c.plain(name)) == h:
            skipped.append(name)
            continue
        patches[name] = data

    if not patches:
        return {"version": manifest.get("version"), "skipped": skipped,
                "patched": [], "written": False}

    report, _, _ = c.replace(patches, strategy="auto", dry_run=dry_run)
    report["version"] = manifest.get("version")
    report["skipped"] = skipped
    report["patched"] = list(patches)

    if not dry_run:
        import json as _json
        stamp = {"version": manifest.get("version"),
                 "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "entries": list(patches)}
        open(os.path.join(client_dir, "L1J_Update.json"), "w", encoding="utf-8").write(
            _json.dumps(stamp, ensure_ascii=False, indent=2))
    return report
