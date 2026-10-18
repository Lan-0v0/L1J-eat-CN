# l1pak — 天堂1 客户端 Text 容器读写（Rust）

与 `L1J-eat-CN` 的 Python 实现（`lib/paktools.py` + `lib/pakcore.py`）**逐字节等价**，
由 `tests/vectors.rs` 的 golden vectors 对拍保证（`tests/vectors.json` 由 Python 侧生成）。

## 能力

| 模块 | 内容 |
|---|---|
| `cipher` | 8 字节分组加密/解密（块间独立、末尾余数原样） |
| `container` | 读 idx/pak、指纹（密文 SHA-256）、原地写、尾部追加、原子保存 |

写入策略与 Python 侧一致：

- **同长度 → 原地写**：只改受影响的 8 字节块，`idx` 一字节不动
- **不同长度 → 尾部追加**：新内容追加到 pak 末尾，只改 1 条 idx 记录的 offset/size

## 快速开始

```bash
export CARGO_HOME=E:/Rust/cargo RUSTUP_HOME=E:/Rust/rustup
export PATH=$PATH:/e/Rust/cargo/bin

cargo test --release          # 7 个测试（向量对拍 / 回环 / 块独立 / 容器行为）
cargo run --release --example bench -- "D:/.../龙潭天堂"   # 性能基线
```

## 坑：源码路径含中文时链接失败

`x86_64-pc-windows-gnu` 的 `ld` 处理不了非 ASCII 路径，会报
`cannot find ...rcgu.o: No such file or directory`（文件其实存在）。

解决：把构建产物放到纯英文目录（本机已在 `E:\Rust\cargo\config.toml` 里设了
`[build] target-dir = "E:/Rust/target"`）。换机器时记得同样设置。

## 密钥表

`src/tables.rs` 由 `lib/gen_tables.py` 从 `lib/maps.json` 生成，**不要手改**。
换表时重跑生成脚本即可。

## 格式依据

`docs/pak-format.md`（idx = 4B 条数 + n×28；条目 8B 分组、块独立、余数原样；
禁止紧凑重建）。
