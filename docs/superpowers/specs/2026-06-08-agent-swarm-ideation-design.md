# Agent Swarm Ideation Platform — Design Spec

- **Date**: 2026-06-08
- **Status**: Ready for user review
- **Author**: brainstorming session
- **Source idea**: `idea.md`

## 1. 概述(Overview)

### 1.1 问题陈述

用户有一个"想法"(可能简单如"做个竞品分析",可能复杂如"搭个技术开发底座")。系统要:

1. 提供**语音对话**作为入口
2. **自动生成 agent swarm** 完成该想法
3. **实时可观测**每个子任务的运行状态
4. 用户可**随时纠正**,且系统应**自我纠错**
5. 完成后语音播报结果

### 1.2 用户画像(已确认)

- 主要用户:用户本人(技术开发者)
- 部署:本地起步,可移植到云
- LLM:云 API + 本地模型,动态切换
- 语音:**必选**,且要求"一边交互,一边干活"
- 范围:全能力,3+ 月达成

### 1.3 选定方案

**方案 A:LiveKit Agents + LangGraph + Langfuse**

理由:

- 满足"语音 + 后台并行" — LiveKit 是**唯一**把 background tasks 写进官方文档的
- 满足"用户纠错" — LangGraph `interrupt/Command` 是同类最干净的 API
- 满足"自纠错" — Reflector 节点
- 满足"可观测" — Langfuse 自带 UI
- 满足"容器化" — e2b + microsandbox
- 3+ 月达成全能力,可移植,生态最大

## 2. 架构(Architecture)

### 2.1 四层架构

```
┌─────────────────────────────────────────────────────────────┐
│ L1. 接入层(多入口)                                            │
│  LiveKit Voice / CLI REPL / 未来 Web                          │
├─────────────────────────────────────────────────────────────┤
│ L2. 编排层(对话流 + 后台并行)                                  │
│  LiveKit Agent(实时 STT/TTS/VAD + BackgroundTask)              │
├─────────────────────────────────────────────────────────────┤
│ L3. 大脑层(动态 Agent Swarm)                                  │
│  LangGraph(Planner/Executor/Reflector/HumanLoop/Synthesizer) │
│  + e2b/microsandbox + LiteLLM + SQLite checkpoint            │
├─────────────────────────────────────────────────────────────┤
│ L4. 观测层                                                    │
│  Langfuse + SQLite                                            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 进程拓扑

| 进程 | 端口 | 启动 | 资源 |
|---|---|---|---|
| LiveKit Server | 7880 + 7881/7882 UDP | docker | ~50MB |
| LiveKit Agent | - | python | ~2GB (Whisper) |
| LangGraph Runtime | 8000 (optional HTTP) | python | ~500MB |
| Langfuse + PG + Redis | 3000 + 5432 + 6379 | docker compose | ~1GB |
| microsandbox | - | SDK | 按需 |

**总内存 ~4GB**

### 2.3 项目结构

```
agent-swarm/
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── README.md
├── voice/                        # L1 + L2
│   ├── agent.py
│   ├── pipeline/
│   │   ├── stt.py
│   │   ├── tts.py
│   │   ├── vad.py
│   │   └── llm_router.py
│   └── background.py
├── graph/                        # L3
│   ├── main_graph.py
│   ├── state.py
│   ├── nodes/
│   │   ├── planner.py
│   │   ├── executor.py
│   │   ├── reflector.py
│   │   ├── human_loop.py
│   │   └── synthesizer.py
│   ├── tools/
│   │   ├── sandbox_e2b.py
│   │   ├── sandbox_local.py
│   │   ├── web_search.py
│   │   └── file_io.py
│   └── checkpointer/sqlite.py
├── observability/
│   ├── langfuse_client.py
│   └── config.py
├── llm/
│   ├── litellm_config.yaml
│   └── prompts/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── scripts/
    ├── start_livekit.sh
    ├── start_langfuse.sh
    └── dev_repl.py
```

### 2.4 核心开源组件

| 组件 | 项目 | 角色 |
|---|---|---|
| LiveKit Server | livekit/livekit | WebRTC 信令 |
| LiveKit Agent | livekit/agents | 语音编排 |
| STT | SYSTRAN/faster-whisper | 中文 STT |
| TTS | rhasspy/piper | 中文 TTS |
| TTS 备选 | home-assistant/edge-tts | 微软 Edge TTS |
| VAD | snakers4/silero-vad | 说话起止 |
| LangGraph | langchain-ai/langgraph | Agent 大脑 |
| LiteLLM | BerriAI/litellm | LLM 路由 |
| e2b | e2b-dev/e2b | 云沙箱 |
| microsandbox | superradcompany/microsandbox | 本地 microVM |
| Langfuse | langfuse/langfuse | 观测 |

## 3. 数据流(Data Flow)

### 3.1 前台语音流(毫秒级)

```
你说话
  │ WebRTC
  ▼
LiveKit Server (room: user-1)
  │
  ▼
LiveKit Agent
  ├─ Silero VAD → 开始说话
  ├─ faster-whisper STT → 文字
  ├─ LiteLLM 判:闲聊 or 新任务
  │   ├─ 闲聊 → Piper TTS 回应
  │   └─ 新任务 → BackgroundTask 启动
  └─ 后台进度 → TTS 主动播报
```

### 3.2 后台 Agent 流(秒-分钟)

```
BackgroundTask
  │
  ▼
LangGraph.invoke({task, thread_id})
  │
  ▼ Planner: 拆解成子任务 DAG
  │
  ▼ Executor(动态 add_node):
  │   for subtask:
  │     根据类型调 sandbox/web_search/file_io
  │   可选 asyncio.gather 并行
  │
  ▼ Reflector:
  │   ├─ complete → Synthesizer → TTS 播报结果
  │   ├─ retry    → 回到 Planner
  │   └─ ask_human→ HumanLoop → interrupt
  │
  ▼ interrupt 触发:
     state 存 SQLite
     等用户回复
     Command(resume=user_input) → Reflector 继续
```

## 4. 错误处理(Error Handling)

| 失败 | 检测 | 恢复 | 用户感知 |
|---|---|---|---|
| Whisper 乱码 | STT 置信度 | TTS: "再说一遍" | 重试 |
| LLM 限流/超时 | LiteLLM | 退避 3 次,切备用模型 | TTS: "切换模型中" |
| Ollama 挂 | health check | 切云 API | TTS: "切到云端" |
| 沙箱启动失败 | SDK | 切另一种沙箱 | 后台日志 |
| Planner 拆错 | Reflector | 自动重拆 | TTS: "重新规划" |
| 子任务超时 | 节点 timeout | 标 failed,继续 | 进度播报 |
| 图死循环 | recursion_limit | 中断 | TTS: "卡住了" |
| 用户不说话 | LiveKit silence | 暂停+询问 | TTS: "还在吗?" |
| 关键决策模糊 | Reflector ask_human | interrupt 暂停 | TTS: "需要你定" |
| LLM 幻觉 | Reflector 自检 | 重跑加严 prompt | 不打扰 |

## 5. State Schema

```python
class AgentState(TypedDict):
    task: str
    subtasks: list[dict]
    current_step: int
    results: dict[str, any]
    sandbox_outputs: list[dict]
    status: Literal["running", "paused", "failed", "complete"]
    pause_reason: str | None
    retry_count: int
    user_corrections: list[dict]
    feedback_for_reflector: str
    thread_id: str
    started_at: str
    last_active: str
    llm_calls: int
```

## 6. 观测(Langfuse)

每个节点上报:

- 输入/输出 state
- LLM 调用(prompt/completion/tokens/latency)
- 工具调用(sandbox 启动/执行/清理)
- 用户纠错事件

Langfuse Web UI 可:

- 全链路 trace
- 对比两次执行
- 评估 reflector 效果

## 7. 测试(Testing)

### 7.1 单元测试(无 LLM)

- State 序列化
- Planner 解析
- Reflector 决策分支
- HumanLoop interrupt/Command
- LiteLLM 路由
- Sandbox 工具

### 7.2 集成测试(便宜 LLM)

- 完整任务跑通
- 纠错流
- 自纠错
- 沙箱回退
- LLM 切换

### 7.3 E2E 测试(真人 + 真实语音)

- 中文识别率 >90%
- 后台+前台并发流畅
- 打断立即停
- 5 分钟任务纠错 3 次仍能完成
- 断网恢复

## 8. 里程碑(Milestones)

### 阶段 1:基础对话(W1-W4)

- W1: LiveKit server + Agent
- W2: Whisper/Piper/VAD
- W3: LiteLLM,Claude + Ollama 切换
- W4: CLI/REPL 入口

**验收:** 文字/语音聊 30 轮+,LLM 切换无感

### 阶段 2:LangGraph 大脑(W5-W8)

- W5: Planner→Synthesizer
- W6: Executor + 工具
- W7: Reflector 自纠错
- W8: LiveKit BackgroundTask 集成

**验收:** 语音说"分析竞品",3 分钟内拿到报告,过程能聊/打断

### 阶段 3:用户纠错(W9-W10)

- W9: HumanLoop + interrupt
- W10: 纠错后 state 恢复

**验收:** 演示"A→改 B→最终 A+B 混合"

### 阶段 4:可观测 + 沙箱(W11-W12)

- W11: Langfuse
- W12: e2b + microsandbox 双沙箱切换

**验收:** idea.md 全能力达成

## 9. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Whisper 中文不准 | 语音体验差 | W4 真实录音测试,不行换 Paraformer |
| LangGraph 曲线陡 | 进度慢 | W5 整周啃官方 examples |
| LiveKit BackgroundTask 不稳 | 语音断流 | W8 压测,不行降级 Pipecat |
| LLM 成本失控 | 钱烧光 | 强制便宜模型 + 计费监控 |
| 沙箱被弄坏 | 主机不稳 | 沙箱 disposable,绝不污染主进程 |

## 10. 文档交付物

- README.md(5 分钟启动)
- docs/architecture.md
- docs/voice-setup.md
- docs/llm-providers.md
- docs/sandbox.md
- docs/observability.md
- CHANGELOG.md

## 11. 关联资料

- `idea.md` — 原始 idea
- `wiki/agentindex.md` — 选型指南
- `wiki/taskgen-agent.md`
- `wiki/dynamicgraph.md`
- `wiki/agentmgmt.md`
- `wiki/containeragent.md`
- `wiki/agentselect.md`
- `wiki/crdexplained-crd.md`
