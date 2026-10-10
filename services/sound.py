"""
提示音 —— 专注结束、休息结束，各响一声。

为什么不用 QtMultimedia：
    打包时它被显式排掉了（见 build_exe.py），只为放个 wav 引入一整套
    多媒体后端不划算。Windows 自带的 winsound 就够了。

音效从哪来（三级兜底，越靠前越优先）：

    1. assets/focus_end.wav · assets/break_end.wav   ← 你自己放的，想换音色就换文件
    2. 同上的位置自动生成的默认音（第一次响的时候写出来）
    3. Windows 系统提示音（连生成都失败，比如 exe 放在只读目录）

第 2 级是关键：开箱就有声音，不用手动准备任何文件；但文件一旦存在就不会被覆盖，
所以想自定义随时可以换。

    python services/sound.py          # 试听两个提示音
"""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ASSETS_DIR

# 音色定义：(频率 Hz, 时长秒)。频率 0 = 停顿。
# 上行的三音 —— 舒缓，用来提示「这轮专注完了，去休息」。
TONE_FOCUS_END = [(659, 0.14), (784, 0.14), (988, 0.26)]
# 双响 + 高音收尾 —— 提神，用来提示「休息结束，回来干活」。
TONE_BREAK_END = [(880, 0.12), (0, 0.07), (880, 0.12), (0, 0.07), (1175, 0.28)]

TONES = {
    "focus_end": TONE_FOCUS_END,
    "break_end": TONE_BREAK_END,
}

# 兜底用的系统提示音（assets 里既没文件、又生成不出来时）
FALLBACK_BEEP = {
    "focus_end": "MB_ICONASTERISK",     # 柔和的「叮」
    "break_end": "MB_ICONEXCLAMATION",  # 更醒目的提示
}

SAMPLE_RATE = 44100


# ────────────────────────── 生成默认音 ──────────────────────────

def _tone_bytes(notes: list[tuple[int, float]], volume: float = 0.32) -> bytes:
    """把音符列表合成成 16 位单声道 PCM。"""
    buf = bytearray()
    for freq, dur in notes:
        n = int(SAMPLE_RATE * dur)
        for i in range(n):
            if freq <= 0:
                buf += b"\x00\x00"
                continue
            # 淡入 8ms / 淡出 25ms —— 不加包络的话首尾会有「咔」的爆音
            env = min(1.0, i / (SAMPLE_RATE * 0.008), (n - i) / (SAMPLE_RATE * 0.025))
            v = int(volume * env * 32767 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            buf += struct.pack("<h", max(-32768, min(32767, v)))
    return bytes(buf)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm)


def _ensure_wav(kind: str) -> Path | None:
    """
    拿到 kind 对应的 wav 路径。没有就生成一个。

    生成失败（目录只读、磁盘满…）返回 None，由调用方退回系统提示音。
    注意写临时文件是在**另一个位置**，不会污染 assets/ 里用户自己的文件。
    """
    target = ASSETS_DIR / f"{kind}.wav"
    if target.exists():
        return target

    for path in (target, Path(tempfile.gettempdir()) / f"focustimer_{kind}.wav"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_wav(path, _tone_bytes(TONES[kind]))
            return path
        except Exception:
            continue
    return None


# ────────────────────────── 播放 ──────────────────────────

def play(kind: str) -> None:
    """
    放一声提示音。kind = "focus_end" | "break_end"。

    永不抛异常 —— 放不出声是小事，不该拖垮计时器。
    """
    if kind not in TONES:
        return
    try:
        import winsound
    except ImportError:          # 非 Windows
        return

    path = _ensure_wav(kind)
    if path is not None:
        try:
            # SND_ASYNC：立刻返回，不卡界面（25 分钟才响一次，但别赌）
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception:
            pass

    try:
        winsound.MessageBeep(getattr(winsound, FALLBACK_BEEP[kind], -1))
    except Exception:
        pass


# ────────────────────────── 试听 ──────────────────────────

def _preview() -> None:
    import time

    print("① 专注结束（该休息了）…")
    play("focus_end")
    time.sleep(1.2)
    print("② 休息结束（该专注了）…")
    play("break_end")
    time.sleep(1.2)
    print(f"\n音效目录：{ASSETS_DIR}")
    print("想换音色就把你自己的 wav 丢进去，同名覆盖即可。")


if __name__ == "__main__":
    _preview()
