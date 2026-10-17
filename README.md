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
| `text build <补丁目录> [--dry-run]` | 只替换目录里提供的同名文件，其余原样保留 |
| `text verify [--patchdir X]` | 校验配对/完整性；给 patchdir 时逐字节比对补丁与容器 |
| `backup list` | 查看自动备份（保留最近 10 份） |
| `backup restore <备份名>` | 回滚到指定备份 |

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
