"""
打包成 exe。

用法：
    python build_exe.py                # 开发自用：单文件版，放 dist/
                                       # （会自动抄一份本机 .env 过去，方便拿真实数据试）
    python build_exe.py --dir          # 开发自用：文件夹版
    python build_exe.py --release      # ★ 发布给别人：文件夹版 + 使用说明 + .env.example
                                       #   最后压成 release/FocusTimer-vX.Y.Z-win64.zip
    python build_exe.py --release --onefile   # 发布版但打单文件

发布版和开发版的区别（重要）：
    · **不抄 .env** —— 你的数据库密码不会跟着发布包走。
      发布版默认用 SQLite（程序目录下的 focus_timer.db），对方不用装 MySQL。
    · 多一个「使用说明.txt」和「.env.example」。
    · 打成一个 zip，可以直接挂 GitHub Release。

产物：
    dist/FocusTimer.exe                    ← 单文件版（放到桌面双击就能开）
    dist/FocusTimer/FocusTimer.exe         ← 文件夹版（启动快，整个文件夹要一起拷）
    release/FocusTimer-vX.Y.Z-win64/       ← 发布版文件夹
    release/FocusTimer-vX.Y.Z-win64.zip    ← 发布版压缩包

首次打包慢（几分钟），之后会快一些。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
RELEASE = ROOT / "release"

# 这些模块是运行时才用到的，PyInstaller 静态分析扫不到，得手动告诉它
HIDDEN_IMPORTS = [
    "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.pymysql",
    "sqlalchemy.dialects.sqlite",      # 默认后端就是它，必须显式带上
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
    # 其他数据库驱动（我们只用 pymysql / sqlite）
    "MySQLdb", "psycopg2", "pysqlite2", "cx_Oracle",
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


def _version() -> str:
    """从 config.py 里读版本号 —— 不 import，免得触发它的环境变量加载。"""
    for line in (ROOT / "config.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def _copy_assets(dst: Path) -> str:
    """音效放在 exe 旁边（不是打进去）—— 想换提示音直接替换 wav 就行。"""
    src = ROOT / "assets"
    if not src.is_dir():
        return "assets/ 里没有音效 —— 首次提示时会自动生成"
    wavs = sorted(src.glob("*.wav"))
    if not wavs:
        return "assets/ 里没有音效 —— 首次提示时会自动生成"
    out = dst / "assets"
    out.mkdir(parents=True, exist_ok=True)
    for w in wavs:
        shutil.copy2(w, out / w.name)
    return f"已放置 {len(wavs)} 个音效 → {out}"


USAGE_TXT = """FocusTimer v{ver} —— 专注计时器
================================================

【怎么用】
双击 FocusTimer.exe。就这样，不用装 Python，也不用装数据库。

首次启动会初始化数据库，稍等几秒就好。
程序会在同目录生成 focus_timer.db —— 你的学习记录全在这个文件里，
备份就是把它拷走，换电脑也是把它带走。

【界面在哪】
启动后是一个 160x160 的悬浮小窗（不带任务栏图标，可能躲在别的窗口下面）。

  拖动    按住小窗任意位置
  单击    开始 → 暂停 → 继续
  双击    打开 / 收起学习看板
  右键    结束并写总结 / 学习面板 / 设置 / 退出

小窗找不到了，就再双击一次 exe —— 它会把已经在跑的那个叫到前面来，
不会开出第二个窗口（数据库只有一个，开两个数据会打架）。

【必看：强制回顾】
点「结束并写总结」后必须写点东西才能保存，不写存不进去。
这是故意的 —— 计时器只是手段，逼自己用自己的话复述一遍才是目的。

【AI 整理（可选，不填也能用）】
不预置任何 API Key。想用 AI 把总结整理成一段通顺的话：
  右键小窗 → ⚙ 设置 → 「AI 整理」
  → 选「DeepSeek 官方 · V4.1 Flash」
  → 把 API Key 粘进去 → 点「测试连接」确认能用 → 保存

Key 存在同目录的 ai_config.json 里（**明文**，别把这个文件发给别人）。
不填也能正常用，只是总结会原样显示、不会被整理成段落。

【想换成 MySQL（可选）】
默认用 SQLite，个人使用足够。要换 MySQL：
  1. 把 .env.example 复制成 .env
  2. 按里面的说明去掉注释、填上你的连接串
  3. 重开程序（表会自动建，库要你自己先建好）

【注意】
· 整个文件夹要一起拷，别只拷 exe —— 音效和配置是放在外面的
· 只拷 exe 出去会怎样：程序会自己新建一个空的 SQLite 库，
  你原来的记录不会跟过去
"""


def _assemble_release(built: Path) -> Path:
    """把构建结果整理成可以直接分发的发布包。"""
    ver = _version()
    out = RELEASE / f"FocusTimer-v{ver}-win64"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    # 1) exe（文件夹版是 exe + _internal 整个目录；单文件版只有一个文件）
    if built.is_dir():
        for item in built.iterdir():
            dst = out / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
    else:
        shutil.copy2(built, out / built.name)

    # 2) 音效
    print("  " + _copy_assets(out))

    # 3) 示例配置 —— ★ 刻意不抄本机的 .env（那里面有数据库密码）
    shutil.copy2(ROOT / ".env.example", out / ".env.example")
    print("  已放置 .env.example（示例配置，不含任何真实密码）")

    # 4) 使用说明
    (out / "使用说明.txt").write_text(USAGE_TXT.format(ver=ver), encoding="utf-8")
    print("  已生成 使用说明.txt")

    # 5) 压缩包
    zip_base = RELEASE / f"FocusTimer-v{ver}-win64"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=out.parent,
                                        base_dir=out.name))
    print(f"  已压缩 → {zip_path.name}")
    return out


def _write_dev_env(built: Path) -> str:
    """
    开发版：给 exe 写一个 .env，让它和源码连**同一个库**。

    ★ 不能直接抄项目里那份 .env —— 用 SQLite 默认值的时候，那份里
      FOCUS_DATABASE_URL 是空的（或整行被注释掉）。抄过去 exe 只会在
      自己旁边新建一个**空库**，你就看不到自己的记录。
      （这个是实测踩出来的：exe 打开里面啥都没有，就是因为这个。）

    所以这里写的是**源码解析出来的那个连接串**。SQLite 一定是绝对路径 ——
    否则 exe 会在自己所在目录找文件，跟源码那份又不是同一个了。
    """
    import config          # 延迟 import：它会读 .env，确定要用时才触发

    url = config.DATABASE_URL
    if url.startswith("sqlite"):
        path = url.split("///")[-1]
        note = f"已生成 .env → exe 与源码共用同一份数据:\n      {path}"
    else:
        note = (
            f"已生成 .env → exe 连的是 MySQL（连接串含密码）\n"
            f"      ⚠ dist/ 只许自己用，别外发。要发布请用："
            f"python build_exe.py --release"
        )

    (built / ".env").write_text(
        "# 由 build_exe.py 自动生成 —— 让 exe 和 `python main.py` 用同一份数据。\n"
        "# 里面是本机信息，别外发。要发布请用：python build_exe.py --release\n"
        f"FOCUS_DATABASE_URL={url}\n",
        encoding="utf-8",
    )
    return note


def _running_exe() -> str:
    """
    打包前检查：dist 里那个 exe 是不是还在跑。

    ★ 为什么必须拦：Windows 上正在运行的 exe 删不掉，rmtree(ignore_errors=True)
      会**静默跳过**它，于是 dist/ 只删了一半；PyInstaller 再往里写就会产出一个
      新旧文件混在一起的坏包 —— 而且你根本看不出来。
      宁可让打包直接失败，也不要给一个跑不起来的 exe。

    判断方法：能不能独占打开这个文件。开着就说明有进程占着。
    """
    for exe in (DIST / "FocusTimer" / "FocusTimer.exe", DIST / "FocusTimer.exe"):
        if not exe.exists():
            continue
        try:
            with open(exe, "rb+"):
                pass
        except OSError:
            return str(exe)
    return ""


def _rescue_dist_data() -> tuple[str, int]:
    """
    清空 dist/ 之前，先看看里面有没有还没搬走的学习记录，有就另存一份。

    ★ 为什么必须做这件事：
      exe 把 SQLite 数据存在**自己旁边**，而 dist/ 是构建产物、每次打包都被整个删掉。
      要是从 dist/ 里直接双击过那个 exe 并记了东西，记录会在下次打包时
      悄无声息地消失 —— 实测就这么丢过。

    返回 (另存到哪, 有多少条记录)。没有数据就是 ("", 0)。
    """
    candidates = [DIST / "focus_timer.db", DIST / "FocusTimer" / "focus_timer.db"]
    db = next((p for p in candidates if p.exists()), None)
    if db is None:
        return ("", 0)

    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        n = int(con.execute("SELECT COUNT(*) FROM `session`").fetchone()[0])
        con.close()
    except Exception:                            # noqa: BLE001
        n = -1          # 读不出来就当里面有东西，保守起见先备份

    if n == 0:
        return ("", 0)  # 空库，直接删掉没关系

    dst = ROOT / f"focus_timer_rescued_{datetime.now():%Y%m%d_%H%M%S}.db"
    shutil.copy2(db, dst)
    return (str(dst), n)


def main() -> int:
    release = "--release" in sys.argv
    if release:
        # 发布版默认文件夹式：单文件版每次启动都要解压 60MB 到临时目录，
        # 别人第一次打开会觉得「怎么半天没反应」。
        onefile = "--onefile" in sys.argv
    else:
        onefile = "--dir" not in sys.argv

    # —— 先看看上一个包是不是还开着 ——
    busy = _running_exe()
    if busy:
        print("!" * 62)
        print("✗ 打包中止：dist 里那个 exe 还在运行")
        print(f"     {busy}")
        print()
        print("   正在运行的 exe 删不掉，dist/ 会只删一半，打出来的包是坏的。")
        print("   请先把它关掉（右下角托盘里找找，或者任务管理器结束 FocusTimer.exe）")
        print("   然后再跑一次打包。你的数据在项目根的 focus_timer.db 里，不受影响。")
        print("!" * 62)
        return 1

    # —— 清空 dist/（顺带把里面的数据救出来）——
    if DIST.exists():
        rescued, n = _rescue_dist_data()
        shutil.rmtree(DIST, ignore_errors=True)
        if rescued:
            print("!" * 62)
            print(f"⚠  dist/ 里有 {n} 条学习记录还没搬走，已经先另存一份：")
            print(f"     {rescued}")
            print()
            print("   原因：exe 把数据存在自己旁边，而 dist/ 每次打包都会整个清空。")
            print("   以后想留着数据用 exe，请用不带 --release 打的包 ——")
            print("   它的 .env 指向项目根目录的 focus_timer.db，重建不会丢。")
            print("!" * 62)
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)

    code = build(onefile=onefile)
    if code != 0:
        print("\n打包失败，看上面的报错")
        return code

    built = DIST if onefile else DIST / "FocusTimer"

    print("\n" + "=" * 56)
    if release:
        print(f"发布版打包完成（v{_version()}）")
        print("=" * 56)
        out = _assemble_release(built)
        print("=" * 56)
        print(f"\n发布目录: {out}")
        print(f"压缩包  : {RELEASE / f'FocusTimer-v{_version()}-win64.zip'}")
        print("\n★ 发布包里**没有**你的 .env —— 数据库密码不会跟着出去。")
        print("  对方解压后双击 exe 即可，默认用 SQLite，不需要装 MySQL。")
        print("  要把压缩包上传到 GitHub Release 的话，记得先确认里面没有")
        print("  ai_config.json（那个文件里有你的 API Key）。")

        # ★ dist/ 到这里只是个中转站，内容已经复制进 release/ 了。
        #   留着它反而是个坑：里面那个 exe 旁边**没有 .env**，一跑就把数据
        #   写进 dist/，下次打包连库一起被清掉 —— 实测就是这么丢的数据。
        if DIST.exists():
            shutil.rmtree(DIST, ignore_errors=True)
            print("\n已清理中转目录 dist/（内容已经在 release/ 里了）")
    else:
        print("打包完成")
        print("=" * 56)
        print(f"产物: {built}")
        print("  " + _write_dev_env(built))
        print("  " + _copy_assets(built))
        print("\n用法：双击 exe。整个文件夹一起拷走才有效。")
        print("      数据存在项目根目录的 focus_timer.db，重新打包不会丢。")

    # 清掉中间产物 —— build/ 里也有个同名 exe，但它缺依赖，永远打不开，
    # 留着只会让人点错。
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)
        print("\n已清理中间产物 build/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
