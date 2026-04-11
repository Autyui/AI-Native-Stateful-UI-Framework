# CortxUI 用户指南

更新时间：2026-04-10  

## 1. 项目简介

CortxUI 是一个把“项目执行日志”转成“可视化、可交互、可回溯流程”的控制台工具。

你可以把它理解为：
1. 给项目生成一个UI，显示执行步骤（Plan/Steps）。
2. 每一步都有状态展示和锚点（Anchor）。
3. 可以从任意满意的锚点回滚并继续执行。
4. UI 用轻量 JSON 协议渲染，便于低成本迭代。

---
![CortxUI_logo](/CortxUI_logo.jpg)
## 2. 当前状态

当前整体完成度约 **90%**，核心能力已可用：
1. 步骤规划：支持本地目录。
2. 步骤执行：支持按步推进，自动保存 checkpoint。
3. 锚点回溯：支持按 `checkpoint_id` / `step_id` 回滚。
4. 协议化 UI：支持 `info_card / terminal / form / fallback`。
5. UI 导出：支持导出/同步到目标项目目录。
6. Bridge 侧车：支持生成 `.aui-dashboard/bridge` 与 `.aui-dashboard/ui`。
7. 背景图：支持独立控制台与 Dashboard 背景图能力。

---

## 3. 快速开始

### 3.1 环境要求

1. Python 3.10+
2. Node.js 20+
3. Windows PowerShell（项目已提供一键脚本）

### 3.2 安装依赖

```powershell
cd "D:\agent\CortxUI"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd frontend
npm install
```

### 3.3 配置 `.env`

至少配置一个可用模型来源，推荐 OpenAI-compatible 方式：

```env
LLM_MODEL=openai:gpt-5.3-codex
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-endpoint
OPENAI_USE_RESPONSES_API=false
OPENAI_COMPAT_USER_AGENT=curl/8.5.0
```

如果使用 DeepSeek，可改为：

```env
LLM_MODEL=deepseek:deepseek-chat
DEEPSEEK_API_KEY=your_deepseek_api_key
# 可选：私有网关或代理地址
# DEEPSEEK_BASE_URL=https://your-deepseek-endpoint/v1
```

兼容说明：
1. `CODEX_MODEL` 可作为 `LLM_MODEL` 回退。
2. `CODEX_API_KEY` / `CODEX_BASE_URL` 会映射到 `OPENAI_*`。

### 3.4 启动方式
### 3.4.1 启动CortxUI
方式 A（推荐，一键双窗口）：

```powershell
.\start-local.cmd
```

方式 B（手动）：

```powershell
# 后端
cd "安装盘:\目录\CortxUI"
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --port 8000

# 前端
cd "安装盘:\目录\CortxUI\frontend"
npm run dev
```

访问地址：
1. 前端：`http://localhost:3001`
2. 后端：`http://127.0.0.1:8000`

---
### 3.4.2 启动项目的UI
1. 安装依赖项：
需要在项目的根目录（打开虚拟环境）再次下载CortxUI中的requirements
2. 启动：
python .aui-dashboard/ui/ui_runner.py
## 4. 常用操作

### 4.1 新建线程并执行

1. 在 StartPanel 输入仓库 URL 或本地目录。
2. 可选填写任务名与备注。
3. 点击 `Run` 后会先执行一步并展示当前 UI。

### 4.2 回滚与继续执行

1. 在锚点列表选择锚点。
2. 可选输入新的 Prompt（不填则直接回滚）。
3. 可继续 `Run Next Step` 按步推进。

### 4.3 导出或同步 UI

1. 在 `Export / Sync UI` 区域填写目标项目根目录。
2. 选择 `Copy` 或 `Sync`。
3. 导出后会写入：
   - `<project-root>/.aui-dashboard/ui/<thread_id>/...`
   - `<project-root>/AUI_UI_ENTRY.json`

### 4.4 生成 Bridge 侧车

1. 在 `Bridge Preview` 配置目标目录和启动命令。
2. 点击 `Generate aui_bridge.py` 或“一键生成到本地项目”。
3. 生成后可在目标项目中运行：

```powershell
python ./.aui-dashboard/ui/ui_runner.py
```

### 4.5 自定义背景图

1. 模板背景图路径：`frontend/public/assets/bg-custom.jpg`
2. 运行时背景图路径：`<project>/.aui-dashboard/ui/assets/bg-custom.jpg`
3. Dashboard 页可直接配置背景 URL 与遮罩透明度（自动保存在浏览器本地）。

---

## 5. 技术栈

后端：
1. FastAPI
2. LangGraph
3. LangChain（OpenAI / Gemini / DeepSeek provider 适配）
4. Python `jsonl` 持久化与线程工作区管理

前端：
1. Next.js 16
2. React 19
3. Tailwind CSS 4

运行侧车（Sidecar）：
1. `aui_bridge.py`：进程桥接与运行控制
2. `ui_runner.py`：Web UI + WebSocket 实时交互
3. `aui_ui_protocol.json`：状态栏、规则、控件协议

---

## 6. 常见技术问题（FAQ）

1. PowerShell 报 `npm.ps1` 禁止执行脚本。  
解决：用 `npm.cmd` 替代，或临时放开策略后再执行。

2. OpenAI-compatible 网关返回 blocked/403。  
解决：确认 `OPENAI_BASE_URL` 指向 `/v1`，并设置 `OPENAI_USE_RESPONSES_API=false`。

3. DeepSeek 配置了但仍走 GPT/OPENAI。  
解决：显式设置 `LLM_MODEL=deepseek:deepseek-chat`，不要只写模型名；系统会按 provider 前缀强制走 DeepSeek 路由。

4. 背景图不显示。  
排查顺序：
   - 确认 `\frontend\public\assets` 下的bg-custom.jpg文件存在。
   - 确认通过 `ui_runner.py` 启动（已挂载 `/assets`）。
   - 检查 `index.html` 的 `--bg-image` 路径是否正确。

5. 日志出现乱码或中英混杂。  
说明：多来自目标项目本身编码链路；AUI 会尽量展示与高亮，但不强行改写目标项目输出编码。回溯时乱码可以尝试再次点击锚点。

6. `sync` 导出担心覆盖。  
说明：`sync` 只同步目标 UI 目录，不改业务源码；仍建议先确认目标路径再执行。

7. requirements中的pywinpty; platform_system == "Windows"，单独下载有问题的话可以只是pip install pywinpty
---

## 7. 未来计划（Roadmap）

短期（当前进行中）：
1. 物理快照恢复彻底落地（有一些小BUG）
2. 进度条的准确性（一些小BUG）
3. 完善“锚点回滚后环境级恢复”策略（不仅回放输入）。
4. 彻底完善锚点放置机制。
5. 增强多轮交互场景的自动引导与恢复体验。

中期：
1. 增加文件/命令级快照回滚能力。
2. 实现CortxUI的用户自定义制作UI功能
3. 对AI类项目应该实现回滚后可重构prompt的功能

长期：
1. 持久化数据库支持（多用户、历史查询、权限隔离）。
2. 更完整的鉴权、审计与安全边界控制。

可能：
1. 发展为AI-Native 项目自治与交互中枢 （集成式项目工具箱？）
---

## 8. 目录参考

核心目录：
1. `app.py`：API 入口
2. `state.py`：LangGraph 状态机
3. `bridge_generator.py`：Bridge/Sidecar 生成逻辑
4. `bridge_template_assets/`：Bridge 模板（含 `index.html`、`ui_runner.py`、`assets/`）
5. `frontend/`：Web 控制台
6. `workplace/`：线程工作区和 UI 产物

---

## 9. 安全边界声明

1. 默认遵循 Non-intrusive 原则：只操作 `.aui-dashboard` 范围。
2. 不会默认修改目标项目业务源码。
3. 执行 `sync`、Bridge 生成等写入操作前，请确认目标目录。
