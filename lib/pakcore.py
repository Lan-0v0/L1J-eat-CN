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


    # ---------- 新增条目 ----------
    def add(self, additions, dry_run=False, backup=True):
        """
        additions: {条目名: 明文 bytes}，只允许**不存在**的条目。

        pak 末尾追加数据；idx 尾部追加 28B 记录，并把头部记录数 +1。
        idx 正文变长 28 字节后不再是 8 的整数倍，因此必须**整段重新加密**
        （paktools.encode 对末尾余数原样保留，与原始 idx 写法一致）。
        写盘前用 _decode_idx 重新解析自检。
        """
        if not additions:
            return {"added": [], "written": False, "pak_old": len(self.pak),
                    "pak_new": len(self.pak), "pak_delta": 0,
                    "idx_size": len(self.idx_raw), "idx_changed": False}, None, None
        for name in additions:
            if name in self.by_name:
                raise SystemExit("条目已存在，不能新增：%s" % name)
            nb = name.encode("utf-8")
            if not (1 <= len(nb) <= NAME_LEN):
                raise SystemExit("条目名长度必须 1..=%d 字节：%s" % (NAME_LEN, name))
            if b"\0" in nb:
                raise SystemExit("条目名不能含 0 字节：%s" % name)

        pak = bytearray(self.pak)
        body = bytearray(self.body)
        added = []
        for name, data in additions.items():
            enc = P.encode(data, 0)
            newoff = len(pak)
            pak += enc
            rec = bytearray(REC_SIZE)
            struct.pack_into("<i", rec, 0, newoff)
            nb = name.encode("utf-8")
            rec[4:4 + len(nb)] = nb
            struct.pack_into("<i", rec, 24, len(enc))
            body += rec
            added.append({"name": name, "size": len(enc), "off": newoff})

        n_new = len(self.recs) + len(additions)
        idx_out = bytearray(4 + len(body))
        struct.pack_into("<I", idx_out, 0, n_new)
        idx_out[4:] = P.encode(bytes(body), 0)
        idx_out = bytes(idx_out)

        # ---- 自检：重新解析 + 逐条回读 ----
        n_chk, body_chk = _decode_idx(idx_out)
        if n_chk != n_new or len(body_chk) != n_new * REC_SIZE:
            raise SystemExit("自检失败：新 idx 结构不符（n=%d）" % n_chk)
        for a in added:
            back = P.decode(bytes(pak[a["off"]:a["off"] + a["size"]]), 0)
            if back != additions[a["name"]]:
                raise SystemExit("自检失败：新增条目回读不一致 %s" % a["name"])

        report = {
            "added": added,
            "pak_old": len(self.pak),
            "pak_new": len(pak),
            "pak_delta": len(pak) - len(self.pak),
            "idx_size": len(idx_out),
            "idx_old_size": len(self.idx_raw),
            "idx_changed": True,
        }
        if dry_run:
            report["written"] = False
            return report, None, None

        if backup:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            bk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backups",
                              "%s_text" % stamp)
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
    # 新增条目：客户端容器里原本没有，合并时走 add 分支（idx 加记录）
    for item in d["added"]:
        name = item["name"]
        plain = b.plain(name)
        path = os.path.join(out_dir, "text", name.replace("\\", "/"))
        os.makedirs(os.path.dirname(path) or os.path.join(out_dir, "text"), exist_ok=True)
        open(path, "wb").write(plain)
        entries.append({"name": name, "size": len(plain), "sha256": sha256(plain), "op": "add"})
    manifest = {
        "schema": 1,
        "container": "Text",
        "version": version or time.strftime("%Y%m%d%H%M"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entries": entries,
        "unsupported": {
            "added": [],
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
    additions = {}
    skipped = []
    for e in manifest.get("entries", []):
        name, size, h = e["name"], e["size"], e["sha256"]
        op = (e.get("op") or "replace").lower()
        p = os.path.join(release_dir, "text", name.replace("\\", "/"))
        if not os.path.isfile(p):
            raise SystemExit("清单里的条目文件缺失：%s" % name)
        data = open(p, "rb").read()
        if len(data) != size or sha256(data) != h:
            raise SystemExit("条目校验失败（大小/哈希不符）：%s" % name)
        exists = name in c.by_name
        if op == "add":
            if exists:
                # 幂等：重跑时条目已在，内容一致就跳过，不一致按替换处理
                if sha256(c.plain(name)) == h:
                    skipped.append(name)
                else:
                    patches[name] = data
            else:
                additions[name] = data
            continue
        if not exists:
            raise SystemExit("客户端容器里没有该条目（清单未标记 add）：%s" % name)
        if sha256(c.plain(name)) == h:
            skipped.append(name)
            continue
        patches[name] = data

    if not patches and not additions:
        return {"version": manifest.get("version"), "skipped": skipped,
                "patched": [], "added": [], "written": False}

    report = {"version": manifest.get("version"), "skipped": skipped,
              "patched": list(patches), "added": list(additions)}
    if patches:
        rep, _, _ = c.replace(patches, strategy="auto", dry_run=dry_run)
        report.update(rep)
    if additions:
        rep2, _, _ = c.add(additions, dry_run=dry_run)
        report["added_report"] = rep2
        report["written"] = rep2.get("written", False)
    report["patched"] = list(patches)
    report["added"] = list(additions)

    if not dry_run:
        import json as _json
        stamp = {"version": manifest.get("version"),
                 "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "entries": list(patches) + list(additions)}
        open(os.path.join(client_dir, "L1J_Update.json"), "w", encoding="utf-8").write(
            _json.dumps(stamp, ensure_ascii=False, indent=2))
    return report
