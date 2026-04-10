# WORKPLACE 运行与目录规范

本项目采用固定工作区模式：
- 固定根目录：`./workplace`
- 默认不允许任意自定义工作目录
- 每个线程独立目录：`workplace/{thread_id}/`
- 线程目录默认友好命名：`task_1`、`task_2`...
- 如果启动时填写任务名，会生成如：`task_3_user_dashboard`

## 1. 目录结构

执行 `POST /run` 或 `POST /plan` 后，系统会自动创建：

```text
workplace/
  {thread_id}/
    repo/        # 预留：用户项目源码拉取位置
    ui/          # 本线程生成的 UI 产物（默认写这里）
    artifacts/   # 步骤输出、计划、中间结果
    logs/        # 线程日志
    generated/
      bridge/    # 桥接脚本与桥接协议预览产物
```

说明：
- `ui/` 是当前默认的 UI 生成目录。
- 不直接写入用户原项目目录，避免误覆盖与安全边界问题。

## 2. 导出/同步到用户目录（显式动作）

系统支持显式导出：
- 接口：`POST /threads/{thread_id}/export-ui`
- 参数：
  - `target_project_root`: 目标项目根目录（推荐）
  - `target_dir`: 自定义导出目录（高级，可选）
  - `mode`: `copy` 或 `sync`

行为说明：
- 当传入 `target_project_root` 时：
  - UI 会落到：`<project_root>/.aui-dashboard/ui/{thread_id}`
  - 同时生成：
    - `<project_root>/AUI_UI_ENTRY.json`（入口索引）
    - `<project_root>/.aui-dashboard/manifest.json`（多线程导出清单）
    - `<project_root>/.aui-dashboard/README_IMPORT.md`（对接说明）
- `copy`: 覆盖复制 UI 文件到目标位置。
- `sync`: 仅同步 UI 子目录（不会删除项目根目录下其它源码文件）。

兼容模式：
- 若只传 `target_dir`，行为与旧版一致，直接导出到该目录。

前端已提供 Export/Sync UI 操作区，无需手动写 curl。

## 3. 一行启动（推荐）

### Windows（项目根目录）

```powershell
powershell -ExecutionPolicy Bypass -File .\Start-Local.ps1
```

或：

```powershell
.\start-local.cmd
```

这会自动打开两个终端窗口：
- 后端：`uvicorn app:app --reload --port 8000`
- 前端：`npm run dev`

注意：
- 该脚本不会自动安装依赖。
- 请先在 `.venv` 中手动执行 `pip install -r requirements.txt`。

## 4. 手动启动（备用）

### 后端

```powershell
cd "D:\agent\AI-Native Stateful UI Framework"
.\.venv\Scripts\Activate.ps1
uvicorn app:app --reload --port 8000
```

### 前端

```powershell
cd "D:\agent\AI-Native Stateful UI Framework\frontend"
npm run dev
```

## 5. 配置与接口

- `GET /workplace`：查看工作区策略。
- `GET /threads/{thread_id}/ui-artifacts`：查看当前线程 UI 产物文件与摘要。
- `POST /threads/{thread_id}/export-ui`：执行导出/同步。
- `GET /threads/{thread_id}/bridge-preview`：基于线程状态生成桥接预览（状态栏阶段 + 启动命令 + 控件）。
- `POST /bridge/preview`：基于 repo_url 直接生成桥接预览（不依赖已存在线程）。
- `POST /threads/{thread_id}/generate-bridge`：将 `aui_bridge.py` 与配置写入目标项目目录。

Bridge 写入路径（非侵入）：
- `<project_root>/.aui-dashboard/bridge/aui_bridge.py`
- `<project_root>/.aui-dashboard/bridge/aui_bridge_config.json`
- `<project_root>/.aui-dashboard/bridge/aui_ui_protocol.json`
- `<project_root>/.aui-dashboard/ui/index.html`（独立 UI 页面）
- `<project_root>/.aui-dashboard/ui/ui_runner.py`（本地 UI 启动器）

独立 UI 使用方式：
```powershell
python .\.aui-dashboard\ui\ui_runner.py
```
页面会直接读取 `../bridge/aui_ui_protocol.json` 与 `../bridge/bridge_runtime.log.jsonl`，显示状态栏与锚点。

## 6. repo_url 输入支持

`POST /run.repo_url` 支持：
- GitHub URL
- 本地目录路径（用于本机测试）

示例：
- `d:\agent\AI-Native Stateful UI Framework`
- `.`
- `local://d:\agent\AI-Native Stateful UI Framework`
