# -*- coding: utf-8 -*-
"""从 maps.json 生成 Rust 静态表 src/tables.rs（避免运行时解析 JSON）"""
import json, os

SRC = r"D:\Game\天堂\L1J-CN\L1J-eat-CN\lib\maps.json"
DST = r"D:\Game\天堂\L1J-CN\L1J-eat-CN\rust\l1pak\src\tables.rs"

m = json.load(open(SRC, encoding="utf-8"))
order = ["Map1", "Map2", "Map3", "Map4", "Map5"]
lines = ["// 由 lib/gen_tables.py 自动生成，请勿手改。", "// 来源：paktools maps.json（l1j-en PakTools.cs 内嵌资源）", ""]
for name in order:
    vals = m[name]
    const = name.upper()
    lines.append("pub static %s: [u8; %d] = [" % (const, len(vals)))
    for i in range(0, len(vals), 24):
        chunk = ", ".join(str(v) for v in vals[i:i + 24])
        lines.append("    " + chunk + ",")
    lines.append("];")
    lines.append("")

os.makedirs(os.path.dirname(DST), exist_ok=True)
open(DST, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("写出 %s  (%d 行)" % (DST, len(lines)))
for name in order:
    print("  %s: %d bytes" % (name.upper(), len(m[name])))
