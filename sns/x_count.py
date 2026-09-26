"""X（旧Twitter）の投稿文字数を公式ルール（twitter-text v3）に沿って数える。

使い方:
    python3 sns/x_count.py "投稿テキスト"
    python3 sns/x_count.py < post.txt

日本語など全角は 2、半角英数・改行は 1、絵文字は 2、URL は長さに関係なく 23 で数え、
上限 280（全角換算 140）に対する残りを表示する。
"""

import re
import sys
import unicodedata

LIMIT = 280
URL_WEIGHT = 23
# twitter-text v3 で重み 1 になる範囲。それ以外は 2。
LIGHT_RANGES = [(0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037)]
URL_RE = re.compile(r"https?://\S+")
# 絵文字の結合文字（異体字セレクタ・ZWJ・肌色修飾）は単独で数えない
EMOJI_JOINERS = {0xFE0F, 0x200D} | set(range(0x1F3FB, 0x1F400))


def weighted_length(text: str) -> int:
    text = unicodedata.normalize("NFC", text)
    total = 0
    for part in URL_RE.split(text):
        for ch in part:
            cp = ord(ch)
            if cp in EMOJI_JOINERS:
                continue
            total += 1 if any(lo <= cp <= hi for lo, hi in LIGHT_RANGES) else 2
    total += URL_WEIGHT * len(URL_RE.findall(text))
    return total


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().rstrip("\n")
    n = weighted_length(text)
    print(f"X換算: {n}/{LIMIT}（全角換算 {n / 2:.1f}/140）残り {LIMIT - n}")
    lines = text.split("\n")
    print(f"行数: {len(lines)}（空行 {sum(1 for l in lines if not l.strip())}）")
    print(f"1行目: {len(lines[0])}文字")


if __name__ == "__main__":
    main()
