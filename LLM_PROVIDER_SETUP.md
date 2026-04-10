# LLM Provider Setup (GPT / Gemini / DeepSeek)

本文档用于本地运行 AUI-Dashboard 时配置模型提供商。

## 1. 先安装基础依赖

```powershell
cd "D:\agent\AI-Native Stateful UI Framework"
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 按需安装模型 Provider（单独安装也可以）

### GPT / OpenAI（含 OpenAI-compatible）

```powershell
pip install -U langchain-openai
```

### Gemini

```powershell
pip install -U langchain-google-genai
```

### DeepSeek

```powershell
pip install -U langchain-deepseek
```

## 3. `.env` 配置示例

你可以只保留一种 Provider 的配置。

### A. GPT（你当前推荐，用 `gpt-5.3-codex`）

```env
# 推荐显式写 provider 前缀
LLM_MODEL=openai:gpt-5.3-codex

# 标准 OpenAI 变量
OPENAI_API_KEY=your_openai_api_key
# 可选：如果使用兼容网关
OPENAI_BASE_URL=https://your-openai-compatible-endpoint
# 默认建议 false（兼容大多数仅提供 chat/completions 的网关）
OPENAI_USE_RESPONSES_API=false
# 某些网关会基于客户端标识做策略拦截，建议设置
OPENAI_COMPAT_USER_AGENT=curl/8.5.0
```

### B. Gemini

```env
LLM_MODEL=google_genai:gemini-2.5-flash
GOOGLE_API_KEY=your_google_api_key
```

### C. DeepSeek

```env
LLM_MODEL=deepseek:deepseek-chat
DEEPSEEK_API_KEY=your_deepseek_api_key
```

## 4. 兼容说明（CODEX_* 变量）

代码已做兼容桥接：
- `CODEX_MODEL` 可作为 `LLM_MODEL` 回退
- `CODEX_API_KEY` 可映射为 `OPENAI_API_KEY`
- `CODEX_BASE_URL` 可映射为 `OPENAI_BASE_URL`

所以如果你当前 `.env` 是：

```env
CODEX_BASE_URL=...
CODEX_API_KEY=...
CODEX_MODEL=gpt-5.3-codex
```

也可以直接运行（建议后续逐步迁移到 `LLM_MODEL` + `OPENAI_*` 命名，便于团队协作）。

网关兼容建议：
- 如果遇到 `Your request was blocked`，优先确认网关是否只支持 `/v1/chat/completions`。
- 本项目默认 `OPENAI_USE_RESPONSES_API=false`，即优先使用 chat/completions 路径。

## 5. 本地启动指令

### 后端

```powershell
cd "D:\agent\AI-Native Stateful UI Framework"
.\venv\Scripts\Activate.ps1
uvicorn app:app --reload --port 8000
```

### 前端

```powershell
cd "D:\agent\AI-Native Stateful UI Framework\frontend"
npm run dev
```

## 6. 本地调用接口示例

### Health

```powershell
curl http://127.0.0.1:8000/health
```

### 运行（本地目录作为 repo）

```powershell
curl -X POST "http://127.0.0.1:8000/run" `
  -H "Content-Type: application/json" `
  -d "{\"repo_url\":\"D:\\agent\\AI-Native Stateful UI Framework\",\"user_notes\":\"请优先读取 AUI_DASHBOARD_PROGRESS.md 并生成可回溯 UI 步骤\",\"max_steps\":1}"
```

### 运行（GitHub URL）

```powershell
curl -X POST "http://127.0.0.1:8000/run" `
  -H "Content-Type: application/json" `
  -d "{\"repo_url\":\"https://github.com/owner/repo\",\"user_notes\":\"生成状态栏与锚点方案\",\"max_steps\":1}"
```
