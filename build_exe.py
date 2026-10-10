"""
打包成 exe。

用法：
    python build_exe.py

产物：
    dist/FocusTimer.exe          ← 单文件版（放到桌面双击就能开）
    dist/FocusTimer/FocusTimer.exe ← 文件夹版（启动快，整个文件夹要一起拷）

首次打包慢（几分钟），之后会快一些。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# 这些模块是运行时才用到的，PyInstaller 静态分析扫不到，得手动告诉它
HIDDEN_IMPORTS = [
    "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.pymysql",
    "pymysql",
    "httpx",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.QtNetwork",     # 单实例检测（QLocalServer / QLocalSocket）
    "winsound",            # 提示音。在 sound.play() 里才 import，静态分析扫不到
]

# Anaconda 环境里装了几百个包，PyInstaller 会顺藤摸瓜全打进去（1GB+）。
# 这些本项目一个都用不到，显式排掉。
EXCLUDES = [
    # 科学计算 / 数据分析
    "numpy", "pandas", "scipy", "sklearn", "matplotlib", "seaborn",
    "statsmodels", "pyarrow", "sympy", "numba",
    # 可视化 / 报表
    "plotly", "bokeh", "altair", "dash",
    # 文档 / 测试工具
    "sphinx", "docutils", "jedi", "parso", "pytest", "unittest",
    "pydoc_data", "doctest",
    # Jupyter / IPython 全家桶
    "IPython", "ipykernel", "jupyter", "notebook", "nbformat",
    "nbconvert", "jupyter_client", "jupyter_core", "ipywidgets",
    # 其他 Anaconda 常带的
    "tornado", "zmq", "gi", "PIL", "Pillow", "cv2",
    "win32com", "pythonwin", "pywin32",
    # Qt 用不到的模块（只留 Core/Gui/Widgets）
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore",
    "PyQt5.QtQml", "PyQt5.QtQuick", "PyQt5.QtQuickWidgets",
    "PyQt5.QtMultimedia", "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtBluetooth", "PyQt5.QtNfc", "PyQt5.QtPositioning",
    "PyQt5.QtSql", "PyQt5.QtTest", "PyQt5.QtDesigner",
    "PyQt5.QtHelp", "PyQt5.QtOpenGL", "PyQt5.QtXmlPatterns",
]


def build(onefile: bool = True) -> int:
    mode = "--onefile" if onefile else "--onedir"
    print(f"开始打包（{mode}）...\n")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        mode,
        "--noconsole",                     # 不要黑框
        "--name", "FocusTimer",
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        f"--specpath={ROOT}",
        # 图标（有就用，没有跳过）
        *([f"--icon={ROOT / 'assets' / 'icon.ico'}"]
          if (ROOT / "assets" / "icon.ico").exists() else []),
        *[f"--hidden-import={m}" for m in HIDDEN_IMPORTS],
        *[f"--exclude-module={m}" for m in EXCLUDES],
        str(ROOT / "main.py"),
    ]

    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def main() -> int:
    onefile = "--dir" not in sys.argv

    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    code = build(onefile=onefile)
    if code != 0:
        print("\n打包失败，看上面的报错")
        return code

    target_dir = DIST if onefile else DIST / "FocusTimer"
    target = DIST / ("FocusTimer.exe" if onefile else "FocusTimer/FocusTimer.exe")

    # 自动把 .env 放到 exe 旁边 —— 每次打包都会清空 dist/，不自动带就得手动拷
    env_src = ROOT / ".env"
    if env_src.exists():
        shutil.copy2(env_src, target_dir / ".env")
        env_note = f"已自动放置 .env → {target_dir / '.env'}"
    else:
        env_note = "⚠️ 没找到 .env，exe 会连不上数据库！"

    # 音效也要放在 exe 旁边（不是打进去）。
    # 放外面有个好处：想换提示音直接替换 wav 就行，不用重新打包。
    # 没有 wav 也没关系，首次响的时候会自动生成。
    assets_src = ROOT / "assets"
    sound_note = "assets/ 里没有音效 —— 首次提示时会自动生成"
    if assets_src.is_dir():
        wavs = sorted(assets_src.glob("*.wav"))
        if wavs:
            dst = target_dir / "assets"
            dst.mkdir(parents=True, exist_ok=True)
            for w in wavs:
                shutil.copy2(w, dst / w.name)
            sound_note = f"已自动放置 {len(wavs)} 个音效 → {dst}"

    print("\n" + "=" * 50)
    print("打包完成")
    print(f"产物: {target}")
    print(env_note)
    print(sound_note)
    print("=" * 50)
    print("\n用法：双击 exe 即可。整个 FocusTimer 文件夹一起拷走才有效。")

    # 清掉中间产物 —— build/ 里也有个同名 exe，但它缺依赖，永远打不开，
    # 留着只会让人点错。
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)
        print("已清理中间产物 build/（别去点里面的 exe，它打不开）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
