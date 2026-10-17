# -*- coding: utf-8 -*-
"""
l1eat — 天堂1 客户端吃档工具（自研，纯 Python，无第三方依赖）

当前支持：Text.pak / Text.idx（文字、页面、界面文本）
规划中：icon / Sprite / Tile / Surf 容器

用法：
  python l1eat.py text info                         查看当前客户端 Text 容器概况
  python l1eat.py text list [--grep 关键字] [--limit N]
  python l1eat.py text export <输出目录> [--grep 关键字]
  python l1eat.py text build <补丁目录> [--dry-run] 把目录里的同名文件吃进容器（只替换提供的文件）
  python l1eat.py text verify [--patchdir 目录]     校验 idx/pak 配对与条目完整性
  python l1eat.py backup list                       查看自动备份
  python l1eat.py backup restore <备份名>           回滚到某次备份

安全设计：
  - build 前自动备份（backups/<时间戳>_text/）
  - 先写临时文件，校验通过后才替换正式文件
  - 校验失败自动回滚，不会留下半成品
  - 支持 --dry-run 只看结果不落盘
"""
import os
import sys
import json
import time
import shutil
import struct
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import paktools as P  # noqa: E402

DEFAULT_CLIENT = r"D:\Game\天堂\L1J-CN\龙潭天堂"
CONFIG_PATH = os.path.join(HERE, "config.json")
BACKUP_ROOT = os.path.join(HERE, "backups")
KEEP_BACKUPS = 10


# ---------- 基础 ----------
def load_client(arg=None):
    if arg:
        return arg
    env = os.environ.get("L1EAT_CLIENT")
    if env:
        return env
    if os.path.isfile(CONFIG_PATH):
        try:
            cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
            if cfg.get("client"):
                return cfg["client"]
        except Exception:
            pass
    return DEFAULT_CLIENT


def text_paths(client):
    pak = os.path.join(client, "Text.pak")
    idx = os.path.join(client, "Text.idx")
    return pak, idx


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024


def load_text(client):
    pak_path, idx_path = text_paths(client)
    if not (os.path.isfile(pak_path) and os.path.isfile(idx_path)):
        raise SystemExit(f"找不到 Text.pak / Text.idx：{client}")
    P._init()
    n, recs = P.parse_text_idx(idx_path)
    pak = open(pak_path, "rb").read()
    return pak_path, idx_path, n, recs, pak


# ---------- 备份 ----------
def do_backup(client, tag="text"):
    pak_path, idx_path = text_paths(client)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_ROOT, f"{stamp}_{tag}")
    os.makedirs(dst, exist_ok=True)
    shutil.copy2(pak_path, os.path.join(dst, "Text.pak"))
    shutil.copy2(idx_path, os.path.join(dst, "Text.idx"))
    # 只保留最近 N 份
    items = sorted([d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d))])
    for old in items[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
    return dst


# ---------- 命令 ----------
def cmd_info(args):
    pak_path, idx_path, n, recs, pak = load_text(args.client)
    mx = max((r[0] + r[2]) for r in recs) if recs else 0
    over = [r for r in recs if r[0] < 0 or r[0] + r[2] > len(pak)]
    print(f"客户端   : {os.path.abspath(args.client)}")
    print(f"Text.pak : {len(pak)}B ({human(len(pak))})")
    print(f"Text.idx : {os.path.getsize(idx_path)}B  声明条目={n}  实际解析={len(recs)}")
    print(f"索引末尾 : {mx}B  pak大小={len(pak)}B  → {'配对正常' if mx == len(pak) else ('越界 %d 字节' % (mx - len(pak)) if mx > len(pak) else '有 %d 字节未引用' % (len(pak) - mx))}")
    print(f"越界条目 : {len(over)} 个" + ("  ⚠ 需要修复" if over else "  ✓"))


def cmd_list(args):
    pak_path, idx_path, n, recs, pak = load_text(args.client)
    rows = recs
    if args.grep:
        g = args.grep.lower()
        rows = [r for r in recs if g in r[1].lower()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"{'offset':>10}  {'size':>8}  name")
    for off, name, size in rows:
        flag = "" if 0 <= off and off + size <= len(pak) else "  [越界]"
        print(f"{off:>10}  {size:>8}  {name}{flag}")
    print(f"--- 共 {len(rows)} 条（总条目 {len(recs)}）")


def cmd_export(args):
    pak_path, idx_path, n, recs, pak = load_text(args.client)
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)
    rows = recs
    if args.grep:
        g = args.grep.lower()
        rows = [r for r in recs if g in r[1].lower()]
    cnt = 0
    for off, name, size in rows:
        if off < 0 or off + size > len(pak):
            print(f"  [跳过越界] {name}")
            continue
        data = P.decode(pak[off:off + size], 0)
        safe = name.replace("\\", "/").lstrip("/")
        dst = os.path.join(outdir, safe)
        os.makedirs(os.path.dirname(dst) or outdir, exist_ok=True)
        open(dst, "wb").write(data)
        cnt += 1
        if cnt % 500 == 0:
            print(f"  已导出 {cnt}", flush=True)
    print(f"导出完成：{cnt} 个文件 → {os.path.abspath(outdir)}")


def build_text(client, patchdir, dry_run=False):
    pak_path, idx_path, n, recs, pak = load_text(client)
    names_in_order = [r[1] for r in recs]

    # 收集补丁文件（按条目名匹配，去重）
    wanted = {}
    for f in os.listdir(patchdir):
        p = os.path.join(patchdir, f)
        if os.path.isfile(p):
            wanted[f] = p
    unknown = [k for k in wanted if k not in names_in_order]
    if unknown:
        print(f"[提醒] 补丁目录里有 {len(unknown)} 个文件不在容器条目中（将忽略）：{unknown[:5]}")
    hit = [k for k in wanted if k in names_in_order]
    if not hit:
        raise SystemExit("补丁目录里没有与容器条目同名的文件，什么都没做。")
    print(f"将替换 {len(hit)} 个条目：{hit[:6]}{' …' if len(hit) > 6 else ''}")

    # 计算新 pak
    out = bytearray()
    newrecs = []
    changed_bytes = 0
    for off, name, size in recs:
        if name in hit:
            data = open(wanted[name], "rb").read()
            enc = P.encode(data, 0)
            changed_bytes += len(data)
        else:
            enc = pak[off:off + size]
        newrecs.append((len(out), name, len(enc)))
        out += enc
    print(f"新 pak 预计 {len(out)}B（原 {len(pak)}B，替换内容 {changed_bytes}B）")

    # 校验 1：结构
    mx = max((r[0] + r[2]) for r in newrecs)
    assert mx == len(out), f"结构校验失败：索引末尾 {mx} != pak {len(out)}"
    assert len(newrecs) == len(recs), "条目数不一致"
    assert [r[1] for r in newrecs] == names_in_order, "条目顺序/名称不一致"

    # 校验 2：替换条目逐字节回读
    for off, name, size in newrecs:
        if name in hit:
            src = open(wanted[name], "rb").read()
            back = P.decode(bytes(out[off:off + size]), 0)
            if back != src:
                raise SystemExit(f"回读校验失败：{name}")

    if dry_run:
        print("--dry-run：校验全部通过，未写盘。")
        return

    # 备份
    bk = do_backup(client, "text")
    print(f"已备份原文件 → {bk}")

    # 写临时文件 → 校验 → 替换
    tmp_pak = pak_path + ".building"
    tmp_idx = idx_path + ".building"
    open(tmp_pak, "wb").write(bytes(out))
    buf = bytearray(4 + len(newrecs) * 28)
    struct.pack_into("<I", buf, 0, len(newrecs))
    for i, (off, name, size) in enumerate(newrecs):
        s = 4 + i * 28
        struct.pack_into("<i", buf, s, off)
        nb = name.encode("utf-8")[:20]
        buf[s + 4:s + 4 + len(nb)] = nb
        struct.pack_into("<i", buf, s + 24, size)
    enc_idx = P.encode(bytes(buf), 4)
    buf[4:] = enc_idx
    open(tmp_idx, "wb").write(bytes(buf))

    # 回读临时 idx 自检
    n2, recs2 = P.parse_text_idx(tmp_idx)
    if n2 != len(newrecs) or len(recs2) != len(newrecs):
        os.replace(bk + os.sep + "Text.pak", pak_path)
        os.replace(bk + os.sep + "Text.idx", idx_path)
        raise SystemExit("新 idx 自检失败，已自动回滚")

    os.replace(tmp_pak, pak_path)
    os.replace(tmp_idx, idx_path)
    print(f"完成：pak={os.path.getsize(pak_path)}B  idx={os.path.getsize(idx_path)}B  条目={len(newrecs)}")


def cmd_build(args):
    build_text(args.client, args.patchdir, dry_run=args.dry_run)


def cmd_verify(args):
    pak_path, idx_path, n, recs, pak = load_text(args.client)
    problems = []
    if n != len(recs):
        problems.append(f"idx 声明 {n} 条，实际解析 {len(recs)} 条")
    over = [r for r in recs if r[0] < 0 or r[0] + r[2] > len(pak)]
    if over:
        problems.append(f"{len(over)} 个条目越界（最大越界 {max(r[0]+r[2] for r in over) - len(pak)} 字节）")
    mx = max((r[0] + r[2]) for r in recs) if recs else 0
    if mx != len(pak):
        problems.append(f"索引末尾 {mx} 与 pak {len(pak)} 不一致")
    # 重名检查
    names = [r[1] for r in recs]
    dup = len(names) - len(set(names))
    if dup:
        problems.append(f"存在 {dup} 个重名条目")

    if args.patchdir and os.path.isdir(args.patchdir):
        for f in os.listdir(args.patchdir):
            p = os.path.join(args.patchdir, f)
            if not os.path.isfile(p):
                continue
            rec = next((r for r in recs if r[1] == f), None)
            if not rec:
                problems.append(f"补丁文件不在容器中：{f}")
                continue
            off, name, size = rec
            back = P.decode(pak[off:off + size], 0)
            if back != open(p, "rb").read():
                problems.append(f"内容与容器不一致：{f}")

    if problems:
        print("校验发现问题：")
        for p in problems:
            print("  ✗", p)
        return 1
    print("校验通过：idx/pak 配对正常，条目完整" + ("，补丁文件与容器内容一致" if args.patchdir else ""))
    return 0


def cmd_backup(args):
    if args.action == "list":
        if not os.path.isdir(BACKUP_ROOT):
            print("暂无备份")
            return
        items = sorted(os.listdir(BACKUP_ROOT))
        for d in items:
            p = os.path.join(BACKUP_ROOT, d)
            if os.path.isdir(p):
                sz = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p) if os.path.isfile(os.path.join(p, f)))
                print(f"{d}   {human(sz)}")
        return
    if args.action == "restore":
        src = os.path.join(BACKUP_ROOT, args.name)
        if not os.path.isdir(src):
            raise SystemExit(f"备份不存在：{src}")
        pak_path, idx_path = text_paths(args.client)
        shutil.copy2(os.path.join(src, "Text.pak"), pak_path)
        shutil.copy2(os.path.join(src, "Text.idx"), idx_path)
        print(f"已回滚 → {os.path.abspath(args.client)}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="l1eat", description="天堂1 客户端吃档工具")
    ap.add_argument("--client", help="客户端目录（默认读取 config.json / 环境变量 L1EAT_CLIENT）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("text", help="Text.pak / Text.idx 操作")
    tsub = t.add_subparsers(dest="action", required=True)

    p = tsub.add_parser("info", help="概况与配对检查")
    p.set_defaults(func=cmd_info)

    p = tsub.add_parser("list", help="列出条目")
    p.add_argument("--grep")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_list)

    p = tsub.add_parser("export", help="导出条目到目录")
    p.add_argument("outdir")
    p.add_argument("--grep")
    p.set_defaults(func=cmd_export)

    p = tsub.add_parser("build", help="把补丁目录里的同名文件吃进容器")
    p.add_argument("patchdir")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_build)

    p = tsub.add_parser("verify", help="校验配对/完整性")
    p.add_argument("--patchdir")
    p.set_defaults(func=cmd_verify)

    b = sub.add_parser("backup", help="备份管理")
    b.add_argument("action", choices=["list", "restore"])
    b.add_argument("name", nargs="?")
    b.set_defaults(func=cmd_backup)

    args = ap.parse_args()
    args.client = load_client(args.client)
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
