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
import pakcore as PC  # noqa: E402

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


def build_text(client, patchdir, dry_run=False, strategy="auto"):
    """把补丁目录里的同名文件写进容器。
    策略 auto：长度相同 → 原地写（idx 不动）；长度不同 → 尾部追加 + 改 1 条 idx 记录
    """
    c = PC.Container(client)
    wanted = {}
    for f in os.listdir(patchdir):
        fp = os.path.join(patchdir, f)
        if os.path.isfile(fp):
            wanted[f] = fp
    unknown = [k for k in wanted if k not in c.by_name]
    hit = [k for k in wanted if k in c.by_name]
    if not hit and not unknown:
        raise SystemExit("补丁目录里没有文件，什么都没做。")

    patches = {k: open(wanted[k], "rb").read() for k in hit}
    additions = {k: open(wanted[k], "rb").read() for k in unknown}
    if patches:
        print("将替换 %d 个条目（明文合计 %s）：%s%s"
              % (len(patches), human(sum(len(v) for v in patches.values())),
                 list(patches)[:6], " …" if len(patches) > 6 else ""))
    if additions:
        print("将新增 %d 个条目（明文合计 %s）：%s%s"
              % (len(additions), human(sum(len(v) for v in additions.values())),
                 list(additions)[:6], " …" if len(additions) > 6 else ""))

    if not dry_run:
        bk = do_backup(client, "text")
        print("已备份原文件 → %s" % bk)

    if patches:
        rep, _, _ = c.replace(patches, strategy=strategy, dry_run=dry_run, backup=False)
        for a in rep["inplace"]:
            print("  [原地写]   %-24s off=%d size=%d" % (a["name"], a["off"], a["size"]))
        for a in rep["appended"]:
            print("  [尾部追加] %-24s %dB → %dB  new_off=%d"
                  % (a["name"], a["old_size"], a["new_size"], a["new_off"]))
        print("pak: %d → %d (Δ%+d)  原有数据改动=%dB  追加=%dB"
              % (rep["pak_old"], rep["pak_new"], rep["pak_delta"],
                 rep["pak_bytes_changed"], rep.get("pak_bytes_appended", 0)))
        print("idx: %s" % ("已改（%d 条记录）" % len(rep["appended"]) if rep["idx_changed"] else "未改动"))
    if additions:
        rep2, _, _ = c.add(additions, dry_run=dry_run, backup=False)
        for a in rep2["added"]:
            print("  [新增条目] %-24s %dB  off=%d" % (a["name"], a["size"], a["off"]))
        print("pak: %d → %d (Δ%+d)  idx: %dB → %dB（+1 条记录）"
              % (rep2["pak_old"], rep2["pak_new"], rep2["pak_delta"],
                 rep2["idx_old_size"], rep2["idx_size"]))
    print("--dry-run：未写盘。" if dry_run else "完成。")


def cmd_diff(args):
    """发版侧：比较两个客户端，产出 release（条目文件 + manifest）"""
    d = PC.diff_clients(args.base, args.target)
    print("base  : %s" % os.path.abspath(args.base))
    print("target: %s" % os.path.abspath(args.target))
    print("变更 %d  新增 %d  删除 %d" % (len(d["changed"]), len(d["added"]), len(d["removed"])))
    for x in d["changed"]:
        print("  ~ %-28s %dB" % (x["name"], x["size"]))
    for x in d["added"]:
        print("  + %-28s %dB  （新增条目，idx +1 条记录）" % (x["name"], x["size"]))
    for x in d["removed"]:
        print("  - %-28s %dB  （不支持删除，仅记录）" % (x["name"], x["size"]))
    if not d["changed"]:
        print("没有需要发布的变更。")
        return 0
    if args.out:
        man = PC.export_changes(d, args.out, version=args.version)
        print("已写出 release → %s    version=%s   条目=%d"
              % (os.path.abspath(args.out), man["version"], len(man["entries"])))
        for e in man["entries"]:
            print("    %-28s %6dB  %s" % (e["name"], e["size"], e["sha256"][:16]))
    else:
        print("（未指定 --out，未写出文件）")
    return 0


def cmd_apply(args):
    """客户端侧：读 release 的 manifest，校验后合并"""
    rep = PC.apply_release(args.client, args.release, dry_run=args.dry_run)
    print("版本: %s" % rep.get("version"))
    if rep.get("skipped"):
        print("已是最新，跳过 %d 条：%s" % (len(rep["skipped"]), rep["skipped"][:5]))
    patched = rep.get("patched") or []
    added = rep.get("added") or []
    if not patched and not added:
        print("无需更新。")
        return 0
    for a in rep.get("inplace", []):
        print("  [原地写]   %-24s off=%d size=%d" % (a["name"], a["off"], a["size"]))
    for a in rep.get("appended", []):
        print("  [尾部追加] %-24s %dB → %dB  new_off=%d"
              % (a["name"], a["old_size"], a["new_size"], a["new_off"]))
    if patched:
        print("pak: %d → %d (Δ%+d)  原有数据改动=%dB  追加=%dB"
              % (rep["pak_old"], rep["pak_new"], rep["pak_delta"],
                 rep["pak_bytes_changed"], rep.get("pak_bytes_appended", 0)))
        print("idx: %s" % ("已改" if rep["idx_changed"] else "未改动"))
    ar = rep.get("added_report") or {}
    if ar.get("added"):
        for a in ar["added"]:
            print("  [新增条目] %-24s %dB  off=%d" % (a["name"], a["size"], a["off"]))
        idx_new = ar["idx_size"]
        idx_old = idx_new - PC.REC_SIZE * len(ar["added"])
        print("pak: %d → %d (Δ%+d)  idx: %dB → %dB（+%d 条记录）"
              % (ar["pak_old"], ar["pak_new"], ar["pak_delta"],
                 idx_old, idx_new, len(ar["added"])))
    print("--dry-run：未写盘。" if args.dry_run else "更新完成。")
    return 0


def cmd_build(args):
    build_text(args.client, args.patchdir, dry_run=args.dry_run, strategy=args.strategy)


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


def cmd_file_pack(args):
    """发版侧：把目录里的任意文件打进 release 的文件通道"""
    m, st = PC.pack_files(args.release, args.src, prefix=args.prefix,
                          exclude=args.exclude, deletes=args.delete,
                          version=args.version, dry_run=args.dry_run)
    print("release : %s" % os.path.abspath(args.release))
    print("来源    : %s%s" % (os.path.abspath(args.src),
                              ("  → 前缀 " + args.prefix) if args.prefix else ""))
    print("文件 %d 个（新增 %d / 更新 %d），删除 %d 项%s"
          % (st["total"], len(st["added"]), len(st["updated"]), st["deletes"],
             "（dry-run，未写盘）" if args.dry_run else ""))
    for e in m["files"]["entries"]:
        print("  %-44s %10d B  %s" % (e["path"], e["size"], e["sha256"][:12]))
    for x in m["files"]["deletes"]:
        print("  - %s" % x)
    print("清单版本: %s    schema=%s" % (m.get("version"), m.get("schema")))
    return 0


def cmd_file_list(args):
    """列出 release 的文件通道内容"""
    m, f = PC.list_files(args.release)
    print("版本 %s | 文件 %d 个 | 删除 %d 项 | 内容目录 %s"
          % (m.get("version"), len(f.get("entries", [])),
             len(f.get("deletes", [])), f.get("base")))
    for e in f.get("entries", []):
        print("  %-44s %10d B  %s" % (e["path"], e["size"], e["sha256"][:16]))
    for x in f.get("deletes", []):
        print("  - %s" % x)
    return 0


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

    p = tsub.add_parser("build", help="把补丁目录里的同名文件吃进容器（原地写/尾部追加）")
    p.add_argument("patchdir")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--strategy", choices=["auto", "inplace"], default="auto",
                   help="auto=同长原地写/变长尾部追加；inplace=只允许同长度")
    p.set_defaults(func=cmd_build)

    p = tsub.add_parser("diff", help="发版侧：比较两个客户端并产出 release")
    p.add_argument("--base", required=True, help="基准客户端目录（线上那一份）")
    p.add_argument("--target", required=True, help="改过的客户端目录")
    p.add_argument("--out", help="release 输出目录（manifest.json + text/）")
    p.add_argument("--version", help="版本号（默认时间戳）")
    p.set_defaults(func=cmd_diff)

    p = tsub.add_parser("apply", help="客户端侧：按 release 清单合并进容器")
    p.add_argument("--release", required=True, help="release 目录（含 manifest.json）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = tsub.add_parser("verify", help="校验配对/完整性")
    p.add_argument("--patchdir")
    p.set_defaults(func=cmd_verify)

    b = sub.add_parser("backup", help="备份管理")
    b.add_argument("action", choices=["list", "restore"])
    b.add_argument("name", nargs="?")
    b.set_defaults(func=cmd_backup)

    fl = sub.add_parser("file", help="任意文件通道（反外挂组件 / 补丁 DLL / 配置）")
    fsub = fl.add_subparsers(dest="action", required=True)

    p = fsub.add_parser("pack", help="把目录下的文件打进 release 的文件通道")
    p.add_argument("--src", required=True, help="要发布的目录（递归）")
    p.add_argument("--release", required=True, help="release 目录（含/将生成 manifest.json）")
    p.add_argument("--prefix", default="", help="写进清单时的前缀目录，如 antibot")
    p.add_argument("--exclude", action="append", default=[], help="排除通配（可多次）")
    p.add_argument("--delete", action="append", default=[], help="要从客户端删除的相对路径（可多次）")
    p.add_argument("--version", help="版本号（默认沿用/时间戳）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_file_pack)

    p = fsub.add_parser("list", help="列出 release 的文件通道")
    p.add_argument("--release", required=True)
    p.set_defaults(func=cmd_file_list)

    args = ap.parse_args()
    args.client = load_client(args.client)
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
