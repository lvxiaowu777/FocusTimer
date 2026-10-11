# 专注计时器 FocusTimer

一个常驻桌面的正方形小窗。点一下开始专注，结束时写一句总结，AI 把它整理成一段通顺的话，并提炼关键词。

**核心不是计时，是强制回顾** —— 计时器只是手段，逼你停下来把学到的东西用自己的话写一遍。

![界面：160×160 圆角悬浮窗，显示倒计时 + 类别累计]

---

## 快速开始

```bash
cd /path/to/FocusTimer
export PYTHONIOENCODING=utf-8        # Windows 控制台中文

python db/database.py                # 首次：建表 + 预置类别
python main.py                       # 启动
```

**不需要装数据库。** 默认用 SQLite，数据就放在程序目录下的 `focus_timer.db`，
一个文件，备份和迁移都是拷走它。想用 MySQL 的话照 `.env.example` 建一个 `.env`
写上连接串就切过去了。

**AI 也不需要配 `.env`** —— 启动后在悬浮窗右键「⚙ 设置 → AI 整理」里填 API Key 就行，
配置会写进同目录的 `ai_config.json`。选「DeepSeek 官方 · V4.1 Flash」预设的话，
只需要粘一个 Key，接口地址和模型名都自动填好；也可以选「自定义」接任何 OpenAI 兼容接口。
旁边有「测试连接」，当场告诉你 Key 和模型能不能用。不填也能用，只是总结不会被整理。

> `ai_config.json` 里是**明文 Key**，别把它发给别人 —— 它已经被 `.gitignore` 挡住了。

### 打包

```bash
python build_exe.py                # 自用版 → app/FocusTimer.exe（★ 日常双击这个）
python build_exe.py --onefile      # 自用版，打成单个 exe（启动慢几秒）
python build_exe.py --release      # 发布版 → release/FocusTimer-vX.Y.Z-win64.zip
```

**自用版**装到 `app/`，并给它写一个 `.env`，写明**源码当前解析出来的连接串** ——
让 exe 和 `python main.py` 用同一份数据。这就是重新打包不丢记录的原因。

> ⚠️ 这里有个坑：早先的实现是直接**抄一份项目 .env** 过去。但用 SQLite 时
> 项目 `.env` 里 `FOCUS_DATABASE_URL` 是空的（默认值就是 SQLite），抄过去
> exe 只会在自己旁边新建一个**空库**，看起来就是「打开什么都没有」。
> 现在改成写解析后的连接串，SQLite 一定是绝对路径，两边指向同一个文件。

**发布版**产出 `release/FocusTimer-vX.Y.Z-win64.zip`，可以直接挂到 GitHub Release：

- **不含 `.env`** —— 你的数据库密码不会跟着出去，对方解压双击就能用（走 SQLite）
- 含 `使用说明.txt` 和 `.env.example`（示例配置，只有注释和占位符）
- 含 `assets/` 音效

自用版的产物含本机信息，**别外发**；要发布请用 `--release`。

### 三个目录各管一件事，别混

```
FocusTimer/
├── focus_timer.db   ★ 你的数据 —— 一个文件装全部记录，备份就是拷走它
├── app/             ★ 自用版程序 —— 日常双击 app/FocusTimer.exe
├── release/           发布包（zip，发给别人的，不带任何数据）
├── dist/  build/      打包中转目录 —— 打完包会自动删掉，正常不存在
└── db/ services/ ui/ assets/ ...   源码
```

| 目录 | 是什么 | 会被构建清掉吗 |
|---|---|---|
| `app/` | **你日常用的 exe** | 会重建，但**数据不在里面**，所以无所谓 |
| `release/` | 发给别人的发布包 | 不会 |
| `dist/` `build/` | PyInstaller 的临时产物 | 打完包就删，**里面不该有 exe** |

`app/` 里那个 exe 旁边有个 `.env`，指向**项目根**的 `focus_timer.db`。
这就是为什么换代码、重新打包，你的记录都还在：

```
打包前: 记录 17  指纹 dcae7abb...
        ↓ 完整重新打包（exe 被换成新的）
打包后: 记录 17  指纹 dcae7abb...   ← 一模一样
```

**换个比方**：`app/` 是手机，`focus_timer.db` 是 SIM 卡。换手机不影响卡里的东西。
（旧版本是"数据存在程序文件夹里"，等于把卡焊死在手机上 —— 换一次丢一次。）

### 三种入口各自的数据位置

| 怎么跑 | 数据文件在哪 | 重建会丢吗 |
|---|---|---|
| `python main.py` | 项目根 `focus_timer.db` | 不会 |
| `app/FocusTimer.exe`（自用版） | **项目根** `focus_timer.db` | 不会 |
| `release/…` 解压给别人 | 各自解压目录里的 | 跟着那个文件夹走，设计如此 |

一句话：**认准 `app\FocusTimer.exe`**。判断方法看它旁边有没有 `.env` ——
有 `.env` 的才是自用版。

### 三道保险

1. **打完包自动删掉中转目录 `dist/`** —— 里面那个 exe 旁边没有 `.env`，
   一跑就把数据写进 `dist/`，下次打包连库一起清。删掉它，想点错都没得点。
2. **打包前检查 exe 是否还在运行** —— 正在运行的 exe 删不掉，`dist/` 会只删一半、
   打出坏包。检测到就直接中止并提示去关掉。
3. **删目录前先抢救数据** —— 万一里面真有记录，另存成
   `focus_timer_rescued_<时间>.db` 放在项目根（空库不备份）。

**自用和发布的区别一句话**：自用的包连项目根那份数据；发布包不带任何数据，
对方从零开始记。

### 换数据库 / 搬记录

```bash
python migrate_db.py --to sqlite --dry-run   # 先看会搬什么，不写任何东西
python migrate_db.py --to sqlite             # 把当前库搬到程序目录下的 focus_timer.db
python migrate_db.py --to "mysql+pymysql://root:密码@localhost:3306/focus_timer"
```

反向往 MySQL 搬也可以，同一个命令换个 `--to`。

- **保持 id 不变** —— `session.category_id`、`item.session_id`、`category.parent_id`
  都不用重映射，省掉一大类难查的 bug
- `category.parent_id` 是自引用外键，先全部以 NULL 插入、第二遍再补父子关系
  （否则子类别排在父类别前面时会违反外键）
- 搬完**对比两边行数**，对不上就报错退出，不静默「成功」
- 目标已有数据时默认拒绝，要清空重来必须显式加 `--force`

## 用法

| 操作 | 效果 |
|---|---|
| **拖动** | 按住悬浮窗任意位置 |
| **单击** | 开始 → 暂停 → 继续（有 250ms 延迟，用来和双击分开） |
| **双击** | 打开/关闭学习面板 |
| **右键** | 结束并写总结 / 📋 学习面板 / ⚙ 设置 / 退出 |

计时结束会弹出总结窗口，**不写就不能保存**。

写完保存后 AI 会把它整理成一段通顺的话，**尽量不动你的原意** —— 只做去冗余、理顺序、
改口语为书面，不添加也不删减信息点。看板上每次专注显示为一张卡片：开始→结束时间、
专注时长、休息时长、整理后的段落，以及这次提炼出的关键词。

原文太短就整理成一整段；原文很长或本身带 Markdown 标题（比如整章的笔记），
则**保留原有标题层次**只润色行文，不会被压成一坨。

**卡片上的「休息」把手动暂停也算进去了** —— 对使用者来说，一次专注里除了「在学习」
的时间，剩下都是「没在学」，再拆成「周期休息」和「手动暂停」两栏是多想一层。
并起来还有个好处，三个数能对上：

```
结束时间 − 开始时间 = 专注 + 休息
```

（想拿纯周期休息的秒数用 `timer.break_seconds()`；显示用的那个是 `timer.rest_seconds()`。）

**暂停计时看得见** —— 暂停时悬浮窗大数字变成「本次已暂停多久」往上走，
下方小字显示这一段专注累计暂停了多久。

**界面字号可调** —— 右键悬浮窗 → ⚙ 设置 → 显示 → 界面字号，四档：
小 88% / 标准 100% / 大 115% / 特大 130%。换档时设置窗口立刻变，
点保存后整个界面都跟着变（字号、内边距、圆角一起缩，不是只有字变大）。
存在 `setting` 表的 `font_scale` 里。

**类别支持二级目录** —— 顶层类别下面可以挂子类别，方便分类管理：

```
Agent 系列          ·  51.9 h   ·  2 子项     ← 分组，显示的是合计
  ├ Agent 主循环    ·  32.4 h
  └ MCP 协议        ·  19.5 h
机器学习            ·   3.1 h                ← 没有子类别，它自己就是叶子
```

- 父类别的时长 = **自己 + 所有子类别**，点进去能看到子类别的全部记录（卡片上标了来自哪一个）
- 父类别是「分组」，**不能直接拿来计时** —— 否则那段时间算给谁就说不清了
- 只允许两级；带子类别的类别不能再被移进别的类别（那样就三级了）
- 删除父类别时，子类别会被**移到顶层**，它们的学习记录一条都不会丢
- 看板左栏右键：置顶 / ➕ 新建子类别 / 📁 移动到… / 重命名 / 删除

专注↔休息切换时会**响一声**（可在设置里关；换 `assets/*.wav` 可换音色）。

**只有一个实例** —— 重复双击 exe 不会开第二个窗口，而是把已在跑的那个叫到前台。
界面找不到了（比如把看板关了）就再双击一次 exe。

**SQLite 那边有三个必须设的 pragma** —— 不设会出真问题：

- `foreign_keys=ON`：**SQLite 默认根本不强制外键**。我们的 `ON DELETE CASCADE`
  和 `ON DELETE SET NULL` 全靠它，不打开等于白写（删了记录，关键词会留下孤儿行）
- `journal_mode=WAL`：读写能并发，后台 AI 整理写库时主线程查询不会被卡住
- `busy_timeout`：撞上写锁时等一会儿再报错，而不是立刻 `database is locked`

另外引擎要带 `check_same_thread=False`：AI 整理跑在后台线程里，
而 SQLite 默认禁止一个连接被别的线程使用。

---

## 技术栈

- **GUI**：PyQt5（无边框 + 置顶 + 圆角半透明）
- **数据**：SQLAlchemy + SQLite（默认，零安装）/ MySQL 8（可选，`.env` 里切换）
- **AI**：DeepSeek（把总结整理成一段通顺的话 + 提炼关键词），接口可在设置里换成任何 OpenAI 兼容服务

## 目录

```
main.py          入口 + 单实例锁 + 全局异常兜底
config.py        配置（数据库后端选择 + AI 配置的读写与优先级）
db/              models.py（4 张表）· database.py（建表 + 幂等迁移 + SQLite pragma）
services/        timer.py（计时业务 + 类别树）· settings.py · sound.py · llm.py（整理成段落）
ui/              floating.py（悬浮窗）· review_dialog.py · dashboard.py（类别树 + 段落卡片）
                 settings_dialog.py（专注节奏 + AI 配置）
assets/          focus_end.wav · break_end.wav（可替换）
build_exe.py     打包（自用版 → app/；--release → release/）
migrate_db.py    换数据库 / 搬记录
app/             自用版程序（自动生成，已被 gitignore）
focus_timer.db   SQLite 数据文件（自动生成，已被 gitignore）
ai_config.json   界面上填的 AI 配置（自动生成，已被 gitignore）
```

## 自检

```bash
python config.py           # 配置是否齐全
python db/database.py      # 数据库连通性
python services/llm.py     # 单独测 AI 整理（会真实调用接口）
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

**AI 失败不影响记录** —— 总结先落库，再调 AI。整理失败只是这条暂时显示原文并标「未整理」，
之后可以在看板上右键单条「重新整理」，或点右上角「✨ 批量整理」一次性补齐。

**单实例靠命名管道** —— `QLocalServer` 占住一个名字；第二个进程探测到就直接退出，
顺便把老实例的窗口叫到前台。没有这个的话，双击两次 exe 会得到两个悬浮窗，
两边各连各的数据库，数据会打架。

**休息时长是反推出来的，不是另存一列**

```
休息 = (结束时间 − 开始时间) − 暂停秒数 − 专注时长
```

`duration_seconds` 是当时按当时的设置定死的，所以这么反推出来的休息时间**永远一致** ——
以后把「专注 25 分 / 休息 5 分」改成别的，历史记录的休息时长也不会跟着变（那才是篡改历史）。

**全局异常兜底** —— PyQt5 对槽函数里没接住的异常默认会 `abort()` 整个进程：
程序毫无征兆地消失，连 `finally` 都不执行。所以 `main.py` 换掉了 `sys.excepthook`，
改成控制台打完整 traceback + 弹窗告知，进程不再被杀。

---

## 文档

需求、规划、功能、开发四份文档在本地知识库中维护（开发文档含踩坑记录：为什么用 PyQt5 不用 PySide6），未随仓库发布。

关键设计决策同时写在代码注释里，见 `db/models.py`、`services/timer.py`。

---

## 许可证

[MIT](LICENSE) © 2026 lvxiaowu777
