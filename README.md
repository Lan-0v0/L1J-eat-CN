# L1J-eat-CN — 天堂1 客户端吃档工具（自研）

把散装补丁文件合并进客户端资源容器的工具，用于替代带毒的 `eat.exe`。

- 纯 Python，标准库实现，**无第三方依赖、无网络行为、不执行任何外部程序**
- 当前支持 **Text.pak / Text.idx**（文字、页面、界面文本）
- 规划：icon → Sprite → Tile → Surf

## 快速开始

```bat
l1eat.bat text info          :: 看当前客户端容器概况
l1eat.bat text verify        :: 校验 idx/pak 配对与完整性
l1eat.bat text build work\patch   :: 把补丁目录里的同名文件吃进容器
```

等价的 Python 调用：

```bat
python l1eat.py text info
python l1eat.py text verify
python l1eat.py text build work\patch --dry-run
```

## 典型工作流（改一个游戏页面）

1. **导出要改的条目**（先看清原内容）
   ```bat
   l1eat.bat text export work\export --grep gh_rk
   ```
2. **修改文件**，放进补丁目录（文件名必须与容器内条目名一致）
   ```
   work\patch\gh_rk-h.html
   ```
3. **先空跑**，确认替换范围和大小
   ```bat
   l1eat.bat text build work\patch --dry-run
   ```
4. **正式吃档**（自动备份 + 写临时文件 + 校验 + 替换）
   ```bat
   l1eat.bat text build work\patch
   ```
5. **校验**
   ```bat
   l1eat.bat text verify --patchdir work\patch
   ```
6. 重启客户端 → 改动生效

## 命令一览

| 命令 | 作用 |
|---|---|
| `text info` | 条目数、pak 大小、索引末尾与 pak 是否配对、越界条目数 |
| `text list [--grep X] [--limit N]` | 列出条目（名称/偏移/大小） |
| `text export <目录> [--grep X]` | 导出条目为明文文件 |
| `text build <补丁目录> [--dry-run] [--strategy auto\|inplace]` | 把目录里的同名文件写进容器（**原地写 / 尾部追加**，不再紧凑重建） |
| `text diff --base A --target B [--out release] [--version V]` | **发版侧**：比较两个客户端，产出 `manifest.json` + `text/<条目>` |
| `text apply --release <目录> [--dry-run]` | **客户端侧**：按清单校验并合并进容器 |
| `text verify [--patchdir X]` | 校验配对/完整性；给 patchdir 时逐字节比对补丁与容器 |
| `backup list` | 查看自动备份（保留最近 10 份） |
| `backup restore <备份名>` | 回滚到指定备份 |

## 写入策略（2026-10-19 重写）

| 策略 | 条件 | pak 变化 | idx 变化 |
|---|---|---|---|
| **原地写** | 新长度 == 旧长度 | 只改受影响的 8 字节块 | **0 字节** |
| **尾部追加** | 长度不同 | 末尾追加，旧数据一字不删 | 改 1 条记录（offset/size） |
| ~~紧凑重建~~ | — | ~~整个文件重排~~ | **已废弃**（会丢未引用空隙） |

格式依据见 `需求文件工具与产出\客户端容器格式_Text.pak_Text.idx_20261019.md`。

## 发版 → 更新 的标准流程

```bat
:: 1) 你在开发机上改完内容后，把改过的文件喂进自己的客户端（可选，用于自测）
l1eat.bat text build work\patch --dry-run

:: 2) 发版：对比"线上版客户端"和"你改过的客户端"，产出 release
l1eat.bat text diff --base "D:\...\龙潭天堂_线上版" --target "D:\...\龙潭天堂" --out release\v2026101901 --version 2026101901

:: 3) 玩家端：登录器下载 release 后调用
l1eat.bat text apply --release release\v2026101901
```

## Rust 核心 `rust/l1pak`（客户端侧合并用）

同一套算法的 Rust 实现，与 Python 侧**逐字节等价**（`cargo test` 对拍 20 条 golden vectors）。

| 指标 | Python | Rust |
|---|---|---|
| 解密吞吐 | ~0.06 MB/s | **~36 MB/s** |
| 加载 idx（692 KB） | ~11.5 s | **62 ms** |

客户端侧合并必须用 Rust 版（或直接把它编进登录器）。详见 `rust/l1pak/README.md`。

## 安全机制

1. build 前自动备份到 `backups/<时间戳>_text/`
2. 先写 `*.building` 临时文件，**校验通过才替换正式文件**
3. 校验项：条目数、名称与顺序、索引末尾 == pak 大小、替换条目回读逐字节一致
4. 任一项失败 → 自动回滚，不留半成品
5. `--dry-run` 可先验算不落盘

## 常见问题

**Q：为什么 rebuild 后 pak 变小了？**
原容器里有 3 处填充间隙（本次 14,057 字节），重建会压缩掉这些无用字节。客户端按索引偏移读取，不受影响；`text verify` 会确认配对正常。

**Q：补丁目录里多放了别的文件会怎样？**
不在容器条目中的文件会被忽略并提示，不会新增未知条目（避免破坏客户端索引契约）。

**Q：怎么回滚？**
`backup list` 找到备份名 → `backup restore <名字>`。

## 目录结构

```
L1J-eat-CN/
  l1eat.py          CLI 主程序
  l1eat.bat         Windows 快捷入口（自动找 Python）
  config.json       默认客户端路径
  lib/paktools.py   idx/pak 加解密与索引解析
  lib/maps.json     解密映射表
  work/             导出/补丁工作区
  backups/          自动备份
```

## 路线图

- [x] Text.pak / Text.idx 补丁与重建
- [ ] icon 容器（.tbt/.ico）
- [ ] Sprite.pak 分卷 + idx
- [ ] Tile.Pak / Surf
- [ ] 一键发布（吃档 + 清单生成）
