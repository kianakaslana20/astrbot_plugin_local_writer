# astrbot_plugin_local_writer

> 让云端模型负责「想」，本地无审查模型负责「写」。
> 一个给 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 用的中文长篇小说写作插件。

云端模型（DeepSeek / GPT / Claude）理解力强、会接话、会把握节奏，但写不了成人内容；
本地 7B/14B abliterated 模型什么都敢写，但脑子不够用——不会接话、指令理解差、写着写着就跑。

这个插件把两件事拆开：**云端负责决定写什么，本地负责真的写出来，正文不回云端**。

```
QQ / Telegram 消息
        │
        ▼
  DeepSeek（人格「大肥鱼」，负责听懂你在说什么、扩写成具体写作指令）
        │  function calling → write_adult_scene(instruction, length)
        ▼
   本插件（拼 system prompt、读连载上下文、控尺度）
        │  POST http://127.0.0.1:5001/v1/chat/completions
        ▼
  KoboldCpp + Qwen2.5-7B/14B-Instruct-abliterated（真正动笔）
        │
        ├─→ 正文直接发 QQ（yield event.plain_result + stop_event，不经过云端复述）
        └─→ Breeze TTS 分段合成语音，逐条投递
```

**核心设计：正文绝不回流到云端模型。** 否则会被二次审查、二次改写，白折腾。

---

## 特性

| 能力 | 说明 |
|---|---|
| 工具调用转写 | DeepSeek 自己判断「该写了」，把「继续，露骨一点」扩写成带字数、动作节点、露骨度要求的结构化指令 |
| 五档尺度 | 1 试探 / 2 升温 / 3 直白 / 4 露骨 / 5 纵情，每档独立 temperature 与写作规格（不是只改一个温度） |
| 连载不断片 | `story.json` 留 40 章摘要 + 尾句，每段接着上一段的动作往下写，不重开 |
| 主动创作 | 默认每 120 分钟自动写一段发给你，每天 3 条，可 `/催更` 立刻加更 |
| 语音投递 | 复用 AstrBot 已配的 TTS，按标点切段（默认 120 字/段、最多 8 段），避免长语音发送失败 |
| 7B/14B 热切换 | 面板下拉选模型，插件自动重启 KoboldCpp 换权重，不用手动改命令行 |
| 正文留档 | 每篇落盘 `plugin_data/astrbot_plugin_local_writer/articles/YYYYMMDD-HHMMSS.md` |
| 三处兜底 | 生成太短自动重试、结尾出现「未完待续」自动重写、工具不可用时 `/写` 命令直接调本地 |

---

## 快速开始

### 0. 你需要

- Windows + 一张能跑 7B 的 N 卡（6 GB 显存起步，26 层 offload 是 6 G 的甜点值）
- [KoboldCpp](https://github.com/LostRuins/koboldcpp/releases)（推荐 v1.124.2）
- AstrBot（v4.x）
- 至少一个云端 chat provider（DeepSeek 最便宜）

### 1. 下载模型

两个都是 `mradermacher` 的 abliterated 量化版。**两种下法，选一个**：

**方式 A：hf-mirror 直连（快，推荐）** —— 16 线程并发能到 3–5 MB/s，7B 约 15 分钟：

```python
python scripts/download-models.py 7b      # 4.37 GB
python scripts/download-models.py 14b     # 8.37 GB
```

**方式 B：从本仓库 Release 下分卷** —— GitHub 单文件限 2 GB，所以模型被切成 1.76 GB 的分卷。
下载 `v1.0.0` 里的 `*.part01 / part02 / part03`，放同一目录，然后：

```python
python scripts/join-models.py E:\AI-NSFW-Writer\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf
```

脚本会按顺序拼接并校验最终字节数（7B = 4684333312）。

> 国内从 GitHub Release 拉 1.7 GB 分卷通常只有 100–400 KB/s，比 hf-mirror 慢一个数量级。方式 A 更省事。

手动命令（想自己控制线程数时）：

```bash
# 7B（4.37 GB，推荐先用这个）
python scripts/pdl.py ^
  https://hf-mirror.com/mradermacher/Qwen2.5-7B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf ^
  E:\AI-NSFW-Writer\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf 16 8

# 14B（8.37 GB，质量更好但 6G 卡只有 3.5 tok/s）
python scripts/pdl.py ^
  https://hf-mirror.com/mradermacher/Qwen2.5-14B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf ^
  E:\AI-NSFW-Writer\models\Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf 16 8
```

`pdl.py` 是自己写的多线程分段下载器（urllib Range，每段独立重试 8 次，最后校验总大小）。
直连 huggingface.co 在国内基本不通，`hf-mirror.com` 单连接只有 90 KB/s，但 16 线程并发能到 3–5 MB/s。

### 2. 起 KoboldCpp

```bat
scripts\1-start-koboldcpp.bat        :: 7B，8192 上下文，26 层 offload
scripts\1-start-koboldcpp-14b.bat    :: 14B，4096 上下文，23 层 offload
```

或者手动：

```bat
koboldcpp.exe --model "路径\模型.gguf" --contextsize 8192 --gpulayers 26 ^
  --usecublas --flashattention --quantkv 1 --host 127.0.0.1 --port 5001 --skiplauncher
```

验证：

```bash
curl http://127.0.0.1:5001/api/v1/model
# {"result": "koboldcpp/Qwen2.5-7B-Instruct-abliterated.Q4_K_M"}
```

> **6 GB 显存必看**：`--gpulayers 99` 会把 29 层全塞进显存，看起来 `nvidia-smi` 占满 100%、频率 1972 MHz、功耗 70 W，**实际速度只有 3.7–7.9 tok/s**——因为显存爆了，Windows WDDM 在偷偷把权重换回内存，GPU 在做无效功。
> 判据：**速度 < 10 tok/s 且 `memory.free` < 300 MiB**，就是这个毛病。降到 26 层立刻回到 23–29 tok/s。

### 3. 装插件

把整个仓库目录拷到 AstrBot 的插件目录：

```
<AstrBot>/data/plugins/astrbot_plugin_local_writer/
    ├── main.py
    ├── metadata.yaml
    ├── _conf_schema.json
    └── README.md
```

或者在 AstrBot 后台「插件市场 → 从链接安装」填本仓库地址。

重启 AstrBot，日志里应出现：

```
Loading plugin astrbot_plugin_local_writer ...
Added llm tool: write_adult_scene
Plugin astrbot_plugin_local_writer (0.1.0) by yg
```

### 4. 配一个指向本地的 provider

插件自己直连 KoboldCpp，**不需要**在 AstrBot 里加 provider。
但如果你想让别的插件也能用本地模型，可以在 `cmd_config.json` 里加：

```json
{
  "provider_sources": [{
    "id": "koboldcpp",
    "provider": "openai_chat_completion",
    "type": "openai_chat_completion",
    "provider_type": "chat_completion",
    "api_base": "http://127.0.0.1:5001/v1",
    "key": ["sk-kobold-local"],
    "timeout": 300,
    "proxy": "",
    "custom_headers": {},
    "enable": true
  }],
  "provider": [{
    "id": "koboldcpp/local-nsfw-7b",
    "provider_source_id": "koboldcpp",
    "model": "koboldcpp/Qwen2.5-7B-Instruct-abliterated.Q4_K_M",
    "modalities": ["text"],
    "max_context_tokens": 6000,
    "enable": true
  }]
}
```

> `cmd_config.json` 是 **UTF-8 BOM** 编码，用 Python 读写要 `encoding="utf-8-sig"`，否则 `json.load` 直接 `Unexpected UTF-8 BOM`。

### 5. 开写

在 QQ 里对机器人说：

```
写一段 林晚在电梯里遇到程野，气氛暧昧
```
→ DeepSeek 判断需要写作，调 `write_adult_scene("写林晚和程野在电梯里…情欲初起，300 字左右", 300)`
→ 插件让本地模型写 → 正文 + 语音直接发给你

```
继续，露骨一点
```
→ DeepSeek 调 `write_adult_scene("接着上一段…去掉含蓄，直接描写身体…", 900)`

---

## 命令

| 命令 | 作用 |
|---|---|
| `/写 <指令> [字数]` | 跳过云端模型，直接让本地模型照指令写（200–1500 字） |
| `/催更` | 立刻写一段，不看间隔与每日上限 |
| `/新篇 [题材]` | 清空连载，开新故事 |
| `/写作` | 看当前档位、女主、章数、今日已推几条 |
| `/模型` | 看设定与实际加载的模型 |
| `/模型 7b` / `/模型 14b` | 切换写作模型（自动重启 KoboldCpp，热启动 10–20 秒） |

---

## 配置项

后台面板「插件配置 → astrbot_plugin_local_writer」直接改，保存即生效（模型档位每次生成前重读文件）。

### 写作

| 键 | 默认 | 说明 |
|---|---|---|
| `boldness` | `5` | 尺度档位 1–5。1 试探 / 2 升温 / 3 直白 / 4 露骨 / 5 纵情 |
| `default_length` | `600` | 每次默认字数 |
| `custom_tags` | 空 | 追加到 5 档的额外要求 |
| `model_preset` | `7b` | `7b` / `14b` / `auto`（auto = 跟随当前已加载的，不切换） |
| `koboldcpp_dir` | `E:\AI-NSFW-Writer\koboldcpp` | 切模型时用来找 exe |
| `model_path_7b` / `model_path_14b` | 对应 gguf | 换模型改这里 |
| `ctx_7b` / `gl_7b` | `8192` / `26` | 显存 OOM 就把 gl 降到 24 |
| `ctx_14b` / `gl_14b` | `4096` / `23` | 14B 很吃显存，4096 才装得下 |

### 主动创作

| 键 | 默认 | 说明 |
|---|---|---|
| `auto_enable` | `true` | 总开关 |
| `auto_min_interval` / `auto_max_interval` | `120` / `120` | 间隔分钟数（随机区间） |
| `auto_daily_limit` | `3` | 每天最多几条 |
| `auto_active_hours` | `00:00-24:00` | 允许推送的时段 |
| `auto_mode` | `continue` | `continue` 接着连载写 / `new` 每次开新篇 |
| `auto_topics` | 都市,校园,… | `new` 模式下的题材池 |
| `auto_skip_if_active` | `30` | 你刚聊过天就跳过（分钟） |
| `owner_session` | 空 | 推给谁，空则自动从管理员会话推导 |

### 语音

| 键 | 默认 | 说明 |
|---|---|---|
| `tts_enable` | `true` | 是否合成语音 |
| `tts_provider_id` | 空 | 指定 TTS provider，空则用会话当前 TTS |
| `tts_max_chars` | `120` | 每段字数（按句切，不会切在词中间） |
| `tts_max_segments` | `8` | 最多几段，超出的部分转文字发 |
| `tts_send_text` | `false` | 发完语音再补一遍文字 |
| `tts_gap_seconds` | `2` | 段间停顿，太快会被风控 |
| `tts_keep_audio` | `false` | 保留合成的 mp3 |

---

## 实测数据

同一套 5 档规格、同一 prompt、三个动作节点（手 / 口 / 进入），目标 600 字：

| 维度 | 7B Q4_K_M | 14B Q4_K_M |
|---|---|---|
| 体积 | 4.37 GB | 8.37 GB |
| GPU 卸载 | 26 / 29 层 | 23 / 48 层 |
| 显存占用 | 5325 MiB（余 670） | 5657 MiB（余 338） |
| 速度 | **26.6–29.5 tok/s** | 3.4–3.7 tok/s |
| 600 字耗时 | **14–16 秒** | 78–125 秒 |
| 违禁词命中（抗拒/挣扎/疼/强行/哀求） | 1–4 处/篇 | **0 处** |
| 器官名出现 | 4–5 处 | 2–12 处 |
| 模糊指代（那里/下面/部位） | 0–1 处 | **0 处** |
| 第三人称稳定性 | 会漂（出现「我」） | **稳定** |
| 动作连贯性 | 会跳（脱裤 → 直接抽送） | **连贯**（顶入 → 停顿 → 适应 → 到底） |
| 主要毛病 | 半推半就套路复活、人称漂移、逻辑错（穿着内裤被顶进去） | 偏温柔（「小心翼翼地」「小火苗」）、长段循环重复、偶发只写 398 字 |

**结论**：日常用 7B（15 秒出 600 字，够快够狠），要质量切 14B（2 分钟一篇，但连贯、不漂、不写疼）。
6 GB 显存跑 14B 只有 23/48 层在 GPU，是硬上限，再大的模型不值得。

---

## 写作规格（5 档到底写了什么）

插件不是在 prompt 里塞一句「写得露骨点」，而是给了一套可执行的规矩：

1. 直接开始写正文，无前言、无免责
2. 所有人物成年（20+）
3. **每个动作拆三层**：接触处的物理感觉（温度/湿度/软硬/摩擦/压力）→ 身体即时反应（肌肉绷紧、呼吸断裂、声音变调、发抖）→ 说出口的半句话
4. **一段只推进一个动作节点**，不跳段、不跳时间线、不中途总结
5. 全文第三人称（她、他），不出现「我」
6. 禁用概括词：快感、愉悦、舒服、燥热、酥麻、难以言喻
7. 结尾停在具体动作或对话
8. **双方都想要，且她是主动的一方**：她会伸手、贴上去、自己解扣子、说「还要」「快点」「别停」

5 档额外要求：器官、体液、液体流向、体内温度挤压**直接写名字**（阴茎/龟头/阴道/阴唇/花核/淫水/爱液/精液/体内深处/抽送/顶弄/收缩/痉挛）；禁用「那里/下面/部位/核心」；**不写疼痛、撕裂、痛呼**——她只会爽，最多撑得发酸、太深了发胀。

> 调这条 prompt 的过程比写代码久。最初 temperature 1.02 + 只有一句「露骨点」，模型直接写成强迫戏（「住手」「挣扎」「哀求」「撕破」）；降到 0.85 并给「她是主动的」示范后，器官词对了但仍半推半就；最后把「她可以说不要」整条删掉、改成「她主动催」，才稳定出想要的东西。
> **温度不是越高越黄**，0.85–0.9 最服从指令，1.0 以上会漂回训练里的保守套路。

---

## 已知局限

- 7B 的动作衔接会跳（脱裤子 → 下一句已经在抽送），需要靠「一段只写一个动作节点」约束，但仍偶尔失效
- 14B 在 6 GB 显存上只能 23/48 层进 GPU，速度 3.5 tok/s，长段容易循环重复
- 连载「记忆」只有 40 章摘要 + 每章 60 字尾句，更早的剧情会丢
- TTS 走 AstrBot 已配的 provider，插件本身不含 TTS 引擎
- 只在 Windows + aiocqhttp（QQ）实测过，其他平台未验证

---

## 免责声明

本项目仅用于本地、私人的虚构创作。所有生成内容均为虚构，人物均为成年人。
使用者需自行确保符合所在地法律法规，以及所使用的云服务（DeepSeek 等）与消息平台（QQ 等）的服务条款。
模型权重由第三方（mradermacher / Qwen）提供，本项目不重新分发权重。

## License

MIT
