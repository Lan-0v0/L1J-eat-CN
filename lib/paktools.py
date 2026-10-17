# -*- coding: utf-8 -*-
"""l1j-en PakTools 的 Text.idx / Pak 解密实现（Python 移植，纯标准库）
来源: https://github.com/l1j-en/launcher  Launcher/Utilities/PakTools.cs
      Map1~Map5 来自 Launcher/Properties/Resources.resx (Base64 嵌入资源)

index 记录布局 (28B): Offset(int32) + FileName(20B UTF-8, NUL填充) + FileSize(int32)
idx 文件: [4B 记录数(UInt32)] + N * 28B  —— "前4字节之后"的整段被 Encode 加密
"""
import struct
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_maps():
    with open(os.path.join(_HERE, "maps.json"), encoding="utf-8") as f:
        m = json.load(f)
    return {k: bytes(v) for k, v in m.items()}


MAP1, MAP2, MAP3, MAP4, MAP5 = None, None, None, None, None


def _init():
    global MAP1, MAP2, MAP3, MAP4, MAP5
    m = _load_maps()
    MAP1, MAP2, MAP3, MAP4, MAP5 = m["Map1"], m["Map2"], m["Map3"], m["Map4"], m["Map5"]


def _sub_4032E0(a1, a2):
    numArray = bytearray(8)
    num = 0
    num1 = 0
    while num1 < 16:
        num2 = a1[num]
        num3 = num2 >> 4
        num4 = num2 % 16
        for i in range(8):
            num5 = num1 * 128 + i
            numArray[i] |= (a2[num5 + num3 * 8] | a2[num5 + (16 + num4) * 8])
        num1 += 2
        num += 1
    return bytes(numArray)


def _sub_403340(a1, a2):
    """a1: int (index), a2: byte[8]"""
    numArray = bytearray(a2[4:8])
    numArray1 = _sub_4033B0(bytes(numArray), a1)
    return bytes([a2[4], a2[5], a2[6], a2[7],
                  numArray1[0] ^ a2[0], numArray1[1] ^ a2[1],
                  numArray1[2] ^ a2[2], numArray1[3] ^ a2[3]])


def _sub_403450(a1):
    return bytes([
        (a1[3] << 7 | (a1[0] & 249 | a1[0] >> 2 & 6) >> 1) & 0xFF,
        ((a1[0] & 1 | a1[0] << 2) << 3 | (a1[1] >> 2 | a1[1] & 135) >> 3) & 0xFF,
        (a1[2] >> 7 | (a1[1] & 31 | (a1[1] & 248) << 2) << 1) & 0xFF,
        (a1[1] << 7 | (a1[2] & 249 | a1[2] >> 2 & 6) >> 1) & 0xFF,
        ((a1[2] & 1 | a1[2] << 2) << 3 | (a1[3] >> 2 | a1[3] & 135) >> 3) & 0xFF,
        (a1[0] >> 7 | (a1[3] & 31 | (a1[3] & 248) << 2) << 1) & 0xFF,
    ])


def _sub_403520(a1):
    return bytes([
        MAP4[a1[0] * 16 | a1[1] >> 4],
        MAP4[4096 + (a1[2] | a1[1] % 16 * 256)],
        MAP4[8192 + (a1[3] * 16 | a1[4] >> 4)],
        MAP4[12288 + (a1[5] | a1[4] % 16 * 256)],
    ])


def _sub_4035A0(a1):
    map3 = bytearray(4)
    for i in range(4):
        num = (i * 256 + a1[i]) * 4
        map3[0] |= MAP3[num]
        map3[1] |= MAP3[num + 1]
        map3[2] |= MAP3[num + 2]
        map3[3] |= MAP3[num + 3]
    return bytes(map3)


def _sub_4033B0(a1, a2):
    numArray = _sub_403450(a1)
    num = a2 * 6
    map5 = bytes([
        numArray[0] ^ MAP5[num], numArray[1] ^ MAP5[num + 1],
        numArray[2] ^ MAP5[num + 2], numArray[3] ^ MAP5[num + 3],
        numArray[4] ^ MAP5[num + 4], numArray[5] ^ MAP5[num + 5],
    ])
    return _sub_4035A0(_sub_403520(map5))


def _sub_403220(src):
    """Decode 单块"""
    numArray = [None] * 17
    numArray[0] = _sub_4032E0(src, MAP1)
    num = 0
    num1 = 15
    while num1 >= 0:
        numArray[num + 1] = _sub_403340(num1, numArray[num])
        num1 -= 1
        num += 1
    t = numArray[16]
    numArray1 = bytes([t[4], t[5], t[6], t[7], t[0], t[1], t[2], t[3]])
    return _sub_4032E0(numArray1, MAP2)


def _sub_403160(src):
    """Encode 单块"""
    numArray = [None] * 17
    numArray[0] = _sub_4032E0(src, MAP1)
    num = 0
    num1 = 0
    while num1 <= 15:
        numArray[num + 1] = _sub_403340(num1, numArray[num])
        num1 += 1
        num += 1
    t = numArray[16]
    numArray1 = bytes([t[4], t[5], t[6], t[7], t[0], t[1], t[2], t[3]])
    return _sub_4032E0(numArray1, MAP2)


def coder(src, index, is_encode):
    n = len(src) - index
    out = bytearray(n)
    if n >= 8:
        blk = bytearray(8)
        length = n // 8
        num = 0
        while length > 0:
            blk[:] = src[num + index: num + index + 8]
            r = _sub_403160(bytes(blk)) if is_encode else _sub_403220(bytes(blk))
            out[num:num + 8] = r
            num += 8
            length -= 1
    rem = n % 8
    if rem > 0:
        n1 = n - rem
        out[n1:n1 + rem] = src[n1 + index: n1 + index + rem]
    return bytes(out)


def decode(src, index):
    return coder(src, index, False)


def encode(src, index):
    return coder(src, index, True)


# ---------- Text.idx ----------

def parse_text_idx(path, verbose=False):
    """返回 (declared_count, [ (offset, filename, size), ... ])"""
    global MAP1
    if MAP1 is None:
        _init()
    d = open(path, "rb").read()
    n = struct.unpack("<I", d[:4])[0]
    # 前 4 字节之后整段被加密
    body = decode(d, 4)
    recs = []
    for i in range(n):
        s = i * 28
        if s + 28 > len(body):
            break
        off = struct.unpack("<i", body[s:s + 4])[0]
        name = body[s + 4:s + 24].split(b"\0")[0].decode("utf-8", errors="replace")
        size = struct.unpack("<i", body[s + 24:s + 28])[0]
        recs.append((off, name, size))
    return n, recs


if __name__ == "__main__":
    import sys
    _init()
    print("maps loaded", len(MAP1), len(MAP2), len(MAP3), len(MAP4), len(MAP5))
    for p in sys.argv[1:]:
        print("====", p)
        n, recs = parse_text_idx(p)
        print("declared", n, "parsed", len(recs))
        for r in recs[:10]:
            print("   ", r)
