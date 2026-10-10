# 专注计时器 FocusTimer

一个常驻桌面的正方形小窗。点一下开始专注，结束时写一句总结，AI 把它拆成一条条知识点。

**核心不是计时，是强制回顾** —— 计时器只是手段，逼你停下来把学到的东西用自己的话写一遍。

![界面：160×160 圆角悬浮窗，显示倒计时 + 类别累计]

---

## 快速开始

```bash
cd /path/to/FocusTimer
export PYTHONIOENCODING=utf-8        # Windows 控制台中文

python db/database.py                # 首次：建库建表 + 预置类别
python main.py                       # 启动
```

## 用法

| 操作 | 效果 |
|---|---|
| **拖动** | 按住悬浮窗任意位置 |
| **单击** | 开始 → 暂停 → 继续（有 250ms 延迟，用来和双击分开） |
| **双击** | 打开/关闭学习面板 |
| **右键** | 结束并写总结 / 📋 学习面板 / ⚙ 设置 / 退出 |

计时结束会弹出总结窗口，**不写就不能保存**。

专注↔休息切换时会**响一声**（可在设置里关；换 `assets/*.wav` 可换音色）。

**只有一个实例** —— 重复双击 exe 不会开第二个窗口，而是把已在跑的那个叫到前台。
界面找不到了（比如把看板关了）就再双击一次 exe。

---

## 技术栈

- **GUI**：PyQt5（无边框 + 置顶 + 圆角半透明）
- **数据**：SQLAlchemy + MySQL 8
- **AI**：DeepSeek（把总结拆成知识点）

## 目录

```
main.py          入口 + 单实例锁
config.py        配置（自动读家目录 study/.env 的 DeepSeek key）
db/              models.py（4 张表）· database.py
services/        timer.py（计时业务）· settings.py · sound.py（提示音）· llm.py（拆条目）
ui/              floating.py（悬浮窗）· review_dialog.py · settings_dialog.py · dashboard.py
assets/          focus_end.wav · break_end.wav（可替换）
```

## 自检

```bash
python config.py           # 配置是否齐全
python db/database.py      # 数据库连通性
python services/llm.py     # 单独测 AI 拆条目
python services/sound.py   # 试听两个提示音
python ui/floating.py      # 单独预览悬浮窗
```

---

## 设计要点

**时长永远是算出来的，不是累加出来的**

```
实时显示 = (now − start_time) − paused_seconds − (暂停中 ? now − paused_at : 0)
```

合盖休眠、系统卡顿、关掉工具，数字都不漂。

**关掉工具再打开，计时还在** —— 启动时查 `status IN ('running','paused')` 的记录接回来。

**强制回顾有两层保险** —— 界面禁用保存按钮 + 数据库 CHECK 约束。

**AI 失败不影响记录** —— 总结先落库，再调 AI；拆不出来只是这次没条目。

**单实例靠命名管道** —— `QLocalServer` 占住一个名字；第二个进程探测到就直接退出，
顺便把老实例的窗口叫到前台。没有这个的话，双击两次 exe 会得到两个悬浮窗，
两边各连各的数据库，数据会打架。

---

## 文档

需求、规划、功能、开发四份文档在本地知识库中维护（开发文档含踩坑记录：为什么用 PyQt5 不用 PySide6），未随仓库发布。

关键设计决策同时写在代码注释里，见 `db/models.py`、`services/timer.py`。

---

## 许可证

[MIT](LICENSE) © 2026 lvxiaowu777
