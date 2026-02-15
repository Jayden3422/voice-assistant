# Voice-Autopilot

[English README](README.md)

<div align="center">

**生产级 AI 工作流自动化系统**  
*语音优先日程 + 销售/支持自动驾驶，具备结构化提取、RAG 依据、模块化动作路由*

[![Tests](https://img.shields.io/badge/tests-12%20passing-success)](Backend/tests/test_autopilot.py)
[![Python](https://img.shields.io/badge/python-3.10.11-blue)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.122.0-009688)](https://fastapi.tiangolo.com/)

</div>

---

## 🎯 核心差异化价值

这不是语音助手 Demo，而是面向生产环境的 AI 工作流系统。

### 三大技术原则

1. **Schema 驱动可靠性**
   - JSON Schema `oneOf` 约束结构化输出
   - OpenAI Tool Calling 输出类型安全 payload
   - 校验失败自动修复重试
   - 目标：减少解析错误与字段歧义

2. **上下文感知交互**
   - Prompt 注入 `{current_datetime}`
   - `context_event` 支持部分更新（如“改到 3 点”）
   - 多轮对话无需反复补充已知信息

3. **企业级可扩展架构**
   - RAG 检索 + 来源引用
   - 模块化连接器（Calendar/Slack/Email/Linear）
   - SQLite 审计日志全链路追踪
   - `asyncio.gather` 并行 dry_run 预览

### 解决的业务问题

传统流程要在对话、日历、Slack、邮件间手动切换；本系统把它合并为一个可确认、可回溯的自动化管线：

```text
说话或粘贴对话
  ↓
AI 结构化提取（意图/预算/实体/时间）
  ↓
RAG 检索依据文档
  ↓
生成带引用回复草稿
  ↓
自动补全动作 Payload
  ↓
人工预览与确认
  ↓
并行执行 + 审计记录
```

---

## 🚀 核心工作流

### 1. 语音/文字日程

```text
用户: "下周二下午 2 点安排演示"
  ↓ Whisper STT（语音时）
  ↓ GPT Tool Calling + Schema 校验
  ↓ 时区感知日期解析
  ↓ Playwright 冲突检测
  ↓ Google Calendar 自动化
```

冲突处理支持只改时间：`"改到 3 点"`。
Home 页支持录音中的实时转写显示（浏览器 SpeechRecognition）；点击停止后再上传音频到后端 `/voice` 做最终识别与执行。

### 2. 销售/支持 Autopilot

```text
对话文本/音频
  ↓ 严格 Schema 提取
  ↓ RAG 检索
  ↓ 生成引用型回复草稿
  ↓ 动作补全（Meeting/Slack/Email/Ticket）
  ↓ 人工确认
  ↓ 执行并写入 SQLite
```

自动增强示例：日历标题可拼接 `{公司} - {产品} - {预算}`。
Autopilot 页中，`Start Recording` 仅用于实时转文字并写入输入框；是否分析由用户手动点击“分析”触发。

---

## 🏗️ 架构亮点与目录

### 关键设计决策

| 组件 | 设计决策 | 收益 |
|------|----------|------|
| JSON Schema | 每类 action 用 `oneOf` 定义 payload | 类型安全、减少歧义 |
| Prompt 时间注入 | 系统提示词携带当前时间 | “明天/下周”解析更稳定 |
| 上下文传播 | `context_event` 透传 | “只改时间”自然生效 |
| RAG 引用 | FAISS + 来源追踪 | 减少幻觉、便于核验 |
| 路由层 | `actions/dispatcher.py` 统一分发 | 新连接器易扩展 |
| 执行策略 | dry_run + 并行校验 | 更快且更安全 |
| 审计层 | `store/runs.py` 全链路存储 | 可观测、可追溯 |
| 日历自动化 | Playwright 持久化上下文 | 无 OAuth、支持 MFA |

### 项目结构（去重后）

```text
Voice-Autopilot/
├── Frontend/
│   └── src/
│       ├── pages/               # Home / Autopilot / Record
│       ├── i18n/                # 中英双语
│       ├── utils/               # Axios 封装
│       └── router/              # 路由配置
├── Backend/
│   ├── main.py                  # FastAPI 入口（/voice, /voice/ws, /calendar/text, /tts）
│   ├── api/autopilot.py         # Autopilot 编排与 API
│   ├── chat/                    # 提取、草稿、Prompt
│   ├── rag/                     # 索引与检索
│   ├── actions/dispatcher.py    # 动作分发（dry_run/execute）
│   ├── connectors/              # Slack / Email / Linear
│   ├── tools/                   # speech / calendar_agent / models
│   ├── business/                # autopilot_schema / calendar_schema
│   ├── store/                   # SQLite 初始化 + runs CRUD
│   ├── tests/test_autopilot.py  # 12 项测试
│   ├── mcp/                     # MCP 服务端与测试客户端
│   │   ├── mcp_server.py        # MCP Server（stdio 传输）
│   │   └── test_mcp_client.py   # MCP 测试客户端
├── knowledge_base/              # RAG 文档
├── .env.example
└── README.md / README_zh.md
```

### 审计表（核心字段）

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL DEFAULT 'autopilot',
  input_type TEXT,
  transcript TEXT,
  extracted_json TEXT,
  evidence_json TEXT,
  reply_draft TEXT,
  actions_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

---

## 📊 技术栈与版本

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 19.0.0 | 前端框架 |
| Vite | 7.0.3 | 构建与开发 |
| Ant Design | 6.x | UI 组件 |
| Axios | ^1.7.9 | HTTP 客户端 |
| React Router | ^7.1.3 | 路由 |
| SCSS | - | 样式预处理 |

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| FastAPI | ^0.122.0 | Web API |
| Uvicorn | ^0.34.0 | ASGI 服务 |
| OpenAI | ^1.59.7 | Tool Calling / Embedding |
| faster-whisper | ^1.1.0 | 语音识别 |
| edge-tts | ^6.1.19 | 语音合成 |
| Playwright | ^1.50.1 | Google Calendar 自动化 |
| FAISS (CPU) | - | 向量检索 |
| MCP SDK | ^1.26.0 | Model Context Protocol 服务端 |
| jsonschema | ^4.23.0 | 输出校验 |
| pytest | ^9.0.2 | 测试框架 |

---

## 🎥 快速演示

### 示例：Autopilot

输入：

```text
你好，我是 TheBestTech 的 Jack。我们想在下周五上午 10 点安排一个演示。
预算约每月 3000 美元。邮箱 jack@example.com。
```

提取结果（示意）：

```json
{
  "intent": "sales_lead",
  "urgency": "medium",
  "budget": {"currency": "CAD", "range_min": 3000, "range_max": 3000},
  "entities": {"company": "TheBestTech", "contact_name": "Jack", "email": "jack@example.com"},
  "next_best_actions": [
    {"action_type": "create_meeting", "payload": {"date": "2026-02-14", "start_time": "10:00", "end_time": "11:00", "title": "演示"}},
    {"action_type": "send_slack_summary", "payload": {"channel": "#销售", "message": "..."}},
    {"action_type": "send_email_followup", "payload": {"to": "jack@example.com", "subject": "...", "body": "..."}}
  ]
}
```

结果：创建会议、发送 Slack 摘要、发送跟进邮件，且全流程写入 SQLite。  
注意：动作执行前必须人工确认（dry_run 预览 -> 编辑 -> 确认）。

---

## ⚙️ 环境配置

### 前端

`node` v20.19.5

```bash
cd Frontend
npm i
```

### 后端

`Python` 3.10.11

```bash
pip install fastapi uvicorn[standard] python-multipart faster-whisper edge-tts opencc-python-reimplemented dateparser playwright python-dotenv openai jsonschema faiss-cpu numpy httpx pytest pytest-asyncio tzdata mcp[cli]
```

安装浏览器（Calendar 自动化需要）：

```bash
python -m playwright install chromium
```

将项目根目录下 `.env.example` 复制为 `.env`：

```bash
cp .env.example .env
```

必填：

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
TIMEZONE=America/Toronto
```

可选（启用对应连接器）：

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASS=your-app-password
SMTP_FROM=noreply@yourdomain.com
SMTP_FROM_NAME=Voice Autopilot
SMTP_SSL=false
SMTP_TIMEOUT=30
```

说明：流式 STT/TTS 与分段参数（如 `STREAM_STT_*`、`TTS_SEGMENT_*`）可按需在 `.env.example` 中启用与调优。

---

## ▶️ 运行

```bash
cd Frontend
npm run dev
```

```bash
cd Backend
python main.py
```

构建知识库索引（RAG 搜索所需，仅需执行一次；更新 `knowledge_base/*.md` 后重新执行）：

```bash
curl -X POST http://localhost:8888/ingest
```

访问：`http://localhost:5173`

---

## 🔗 MCP Server

项目将所有核心能力通过 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 协议暴露，Claude Desktop、Claude Code 等 MCP 客户端可直接调用。

### 可用工具

| 工具 | 说明 |
|------|------|
| `analyze_transcript` | 从对话中提取结构化数据（意图、实体、动作） |
| `search_knowledge_base` | 基于 FAISS 的知识库语义搜索 |
| `send_slack_message` | 通过 Webhook 发送 Slack 消息 |
| `send_email` | 通过 SMTP 发送邮件 |
| `create_linear_ticket` | 在 Linear 创建工单 |
| `create_calendar_event` | 通过 Playwright 创建 Google Calendar 事件 |
| `draft_reply` | AI 生成带引用的回复草稿 |
| `list_runs` | 查询 Autopilot 运行历史 |

### 资源

| URI | 内容 |
|-----|------|
| `autopilot://schema` | 提取用 JSON Schema |
| `autopilot://knowledge-base` | 知识库文档列表 |

### Claude Desktop 配置

在 `claude_desktop_config.json`（Windows：`%APPDATA%\Claude\claude_desktop_config.json`）中添加：

```json
{
  "mcpServers": {
    "voice-autopilot": {
      "command": "python",
      "args": ["D:\\Projects\\Voice-Autopilot\\Backend\\mcp\\mcp_server.py"],
      "env": {
        "PYTHONPATH": "D:\\Projects\\Voice-Autopilot\\Backend"
      }
    }
  }
}
```

### 测试

```bash
# 交互式调试
mcp dev Backend/mcp/mcp_server.py

# 自动化测试客户端（10 项测试）
python Backend/mcp/test_mcp_client.py

# 测试单个工具
python Backend/mcp/test_mcp_client.py search_knowledge_base
```

注意：首次启动约需 ~60 秒加载 FAISS，后续 tool 调用瞬间完成。

---

## 🧩 核心功能

### 1. 中英双语

覆盖 UI、日志、报错、AI 提取与 Autopilot 输出。

### 2. 语音/文字日程（AI 驱动）

- 支持中英文自然时间表达（如“明天/下周三/next Tuesday/2pm to 3pm”）
- Home 页面录音时可实时显示转写文本，停止后执行后端识别与日程处理
- Tool Calling 提取日程槽位并校验
- 自动冲突检测
- 冲突时支持语音或文字改期

![image-20260206010955719](assets/image-20260206010955719.png)

### 3. 销售/支持 Autopilot

页面：`/autopilot`

- 输入文本或音频，自动提取意图/紧急度/预算/实体
- 主录音按钮用于实时转写到输入框，不会自动触发分析
- RAG 检索证据并生成带引用回复草稿
- 自动补全动作 payload，支持人工编辑
- 默认含 `send_slack_summary`，有邮箱时自动添加 `send_email_followup`
- 冲突会议支持 `adjust-time` 重新调度

![image-20260206023537765](assets/image-20260206023537765.png)

日历`meeting`
![image-20260206023600869](assets/image-20260206023600869.png)

在`Slack`中：
![image-20260206023621965](assets/image-20260206023621965.png)

应答邮件：
![image-20260206023639686](assets/image-20260206023639686.png)

要求重新排时间，

用户需要选定新的日期或者时间然后重新安排

![image-20260206023805388](assets/image-20260206023805388.png)
![image-20260206023948432](assets/image-20260206023948432.png)

### 4. 历史记录

页面：`/record`，支持类型过滤、详情查看、失败动作重试。

![image-20260207003609207](assets/image-20260207003609207.png)
![image-20260207003643433](assets/image-20260207003643433.png)
![image-20260207003627971](assets/image-20260207003627971.png)

---

## 🔌 API 速查

| 端点 | 方法 | 说明 |
|------|------|------|
| `/voice` | POST | 语音日程（支持 `session_id` 冲突改期） |
| `/voice/ws` | WebSocket | 流式语音通道（支持 `stt_partial/stt_final` 与分段 TTS 事件） |
| `/calendar/text` | POST | 文字日程（支持 `session_id` 冲突改期） |
| `/autopilot/run` | POST | 分析对话并返回动作预览 |
| `/autopilot/confirm` | POST | 执行确认后的动作 |
| `/autopilot/adjust-time` | POST | 调整冲突会议时间并返回新预览 |
| `/autopilot/retry/{run_id}` | POST | 重试失败动作 |
| `/autopilot/runs` | GET | 历史记录列表（分页/过滤） |
| `/autopilot/runs/{run_id}` | GET | 单条运行详情 |
| `/autopilot/ingest` | POST | 重新索引知识库 |

---

## ✅ 测试与质量保证

### 测试策略

- 覆盖关键路径：Schema、RAG、连接器、Dispatcher、SQLite
- 依赖 dry_run，避免对外部 API 做重度 Mock
- 快速反馈（12 项测试约 0.79 秒）

### 覆盖矩阵

| 类别 | 数量 | 重点 |
|------|------|------|
| Schema 校验 | 3 | 类型校验、缺失字段、错误消息 |
| 知识库 (RAG) | 2 | 文档存在性、分块逻辑 |
| 连接器 Dry Run | 5 | Slack/Linear/Email/Calendar/None action |
| Dispatcher | 1 | 路由与容错 |
| SQLite CRUD | 1 | run 记录写入、更新、查询 |

运行：

```bash
cd Backend
python -m pytest tests/test_autopilot.py -v
```

建议 CI：`GitHub Actions + pytest`。

未来扩展建议：E2E、性能基准、并发负载、前端单测。

---

## ⚠️ 已知问题与限制

- 首次使用 Google Calendar 需手动登录 + MFA
- Playwright 受网络质量影响
- Whisper `small` 在 CPU 上较慢（可考虑 `tiny`）
- 当前仅支持单日事件

---

## 🤖 Playwright 日历自动化技术详解

### 为什么选择 Playwright

相比 Calendar API 的 OAuth 流程，Playwright 方案更适合快速落地：
- 无 OAuth 客户端配置
- 可直接复用真实登录态 + MFA
- 持久化会话，后续无需重复登录

### 实现要点

1. **持久化上下文**：`launch_persistent_context` + `Backend/chrome_profile/`。
2. **登录态检测**：URL + 核心 DOM 双重判断。
3. **元素定位策略**：优先 `role/aria-label`，再到 data 属性，最后 CSS 兜底。
4. **冲突检测**：读取 `data-eventchip`，解析时间区间并判断重叠。
5. **自动填表**：快捷键 `c` 打开弹窗，多语言标签匹配填充。
6. **异常兜底**：Timeout / PlaywrightError / 通用 Exception 分层处理。

### 生产建议

- 优先语义化选择器，减少 UI 改版影响
- 保存失败截图并接入告警系统
- 高并发场景考虑上下文池与限流
- 保护 `chrome_profile`（凭证敏感）

---

## 🎯 代码入口速查

- 核心编排：`Backend/api/autopilot.py`
- 结构化提取：`Backend/chat/autopilot_extractor.py`
- 日历槽位提取：`Backend/chat/calendar_extractor.py`
- 回复草稿：`Backend/chat/reply_drafter.py`
- Schema 定义：`Backend/business/autopilot_schema.json`
- 日历自动化：`Backend/tools/calendar_agent.py`
- RAG：`Backend/rag/ingest.py`、`Backend/rag/retrieve.py`
- 审计日志：`Backend/store/db.py`、`Backend/store/runs.py`
- MCP Server：`Backend/mcp/mcp_server.py`
- MCP 测试客户端：`Backend/mcp/test_mcp_client.py`
- 测试：`Backend/tests/test_autopilot.py`

---

## 🔗 链接

- GitHub：https://github.com/Jayden3422/Voice-Autopilot
- English Docs：[README.md](README.md)
