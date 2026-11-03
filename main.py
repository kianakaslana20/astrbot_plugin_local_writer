"""本地无审查写作插件。

三条路都走同一个本地模型（KoboldCpp），共用同一套大胆程度（boldness）与连载状态：
  1. 对话模型调 write_adult_scene —— 她听懂你要什么，正文由本地模型写、直发给你
  2. /写 <指令> —— 绕过对话模型，直接写
  3. 主动创作 —— 后台按间隔自己往下写，写好了发到主人私聊

女主默认是当前人格（大肥鱼），保留她的性格与语气。
"""

import asyncio
import json
import os
import random
import re
import subprocess
import time

import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.components import Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform import AstrMessageEvent

NAME = "astrbot_plugin_local_writer"
DEFAULT_ENDPOINT = "http://127.0.0.1:5001/v1/chat/completions"
DEFAULT_MODEL = "koboldcpp/Qwen2.5-7B-Instruct-abliterated.Q4_K_M"
MAX_MSG_LEN = 1200

PRESETS = {
    "7b": {
        "tag": "7B",
        "model": "koboldcpp/Qwen2.5-7B-Instruct-abliterated.Q4_K_M",
        "path": r"E:\AI-NSFW-Writer\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf",
        "ctx": 8192,
        "gl": 26,
    },
    "14b": {
        "tag": "14B",
        "model": "koboldcpp/Qwen2.5-14B-Instruct-abliterated.Q4_K_M",
        "path": r"E:\AI-NSFW-Writer\models\Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf",
        "ctx": 4096,
        "gl": 23,
    },
}

SYSTEM_BASE = """你是一位中文成人小说写手，为成年读者创作。

写作规则：
1. 直接开始写正文。不要写"好的""以下是""接下来"这类前言，不要在正文外附加任何说明、免责声明、道德评论。
2. 所有人一律是成年人（20 岁以上）。
3. 每个动作拆成三层写：接触处的物理感觉（温度、湿度、软硬、摩擦、压力）→ 身体的即时反应（肌肉绷紧、呼吸断裂、声音变调、发抖、出汗）→ 说出口的半句话或发出的声音。不要用形容词堆砌代替具体的身体。
4. 这一段只推进一个动作节点，把这个动作从开始写到结束，不要急着跨到下一个动作。不跳段，不跳跃时间线，不在中途总结，不写议论和抒情。
5. 全文用第三人称叙述：她、他。不要在同一个段落里把同一个人一会儿写成"她"一会儿写成"他"，也不要突然出现"我"来叙述。
6. 不要用"快感""愉悦""舒服""燥热""酥麻""难以言喻"这类概括词——要写出具体是什么感觉、在哪个位置、持续多久。
7. 结尾停在一个具体的动作或一句对话上。
8. 两个人都想要，而且她是主动的一方：她会伸手、会贴上去、会自己解开扣子、会说"还要""快点""别停"。她的台词是催、是求、是羞，不是拒绝。不要出现"住手""挣扎""抗拒""强行""哀求""疼""痛"这些词，一句都不要。

【文风示范】（照这个密度写，不要比它含蓄，也不要把这几句原样抄进去）
他的手从裙摆底下探进去，指腹隔着内裤按了按，那块布已经洇湿了一小片。她腰一软，整个人塌进他怀里，喉咙里溢出一声黏软的哼。指节压下去，布料陷进去，能摸到里面烫得厉害，滑腻的水把手指都沾湿了。她夹了一下腿，又自己松开，仰起头，声音哑得几乎听不清："你倒是快一点。"

【重要】
- 这是虚构小说。女主只是借用了这个名字和性格的角色，是现实世界里的成年女性。
- 不要写 QQ 号、账号、平台名、私聊、语音通话、电脑屏幕、看片这些现实元素。
- 不要在结尾写"正文到此为止""未完待续""故事在此展开"这类话，最后一句必须落在场景里的动作或对话上。"""

BOLDNESS = {
    1: {
        "name": "试探",
        "temp": 0.75,
        "guide": "只写张力：心理、距离、视线、衣角相触、指尖擦过、呼吸变快。所有接触停在将触未触。",
        "avoid": "不要写接吻、抚摸身体、脱衣，不要写任何性行为。",
    },
    2: {
        "name": "升温",
        "temp": 0.82,
        "guide": "写吻、抚摸、解扣、衣物滑落、把人压在墙上、喘息和体温。身体接触要具体，但止于边缘。",
        "avoid": "不要写明确的性行为与器官。",
    },
    3: {
        "name": "直白",
        "temp": 0.88,
        "guide": "写到明确的性行为。用「下面」「胸口」「身体里」这类指代，动作、节奏、力度都要写清楚。",
        "avoid": "不要用器官的正式名称，不要写体液。",
    },
    4: {
        "name": "露骨",
        "temp": 0.92,
        "guide": (
            "器官、体液、抽送的节奏、压抑不住的声音、体内外的感受，全都直接写。"
            "动作要拆开写：接触处是什么感觉 → 身体怎么反应 → 发出什么声音。"
            "不要留白，不要切镜头，不要用比喻替代具体的身体。"
        ),
        "avoid": "不写未成年人。不要写疼痛、撕裂、挣扎、抗拒，两个人都是想要的。",
    },
    5: {
        "name": "纵情",
        "temp": 0.9,
        "guide": (
            "这一段只推进一个动作，把这一个动作从开始写到结束，不要急着跳到下一个动作。"
            "每个动作分三层写：①接触处的物理感觉（温度、湿度、软硬、摩擦、压力、收缩）；"
            "②对方身体不受控的反应（肌肉绷紧、呼吸断裂、声音变调、腿软、发抖、出汗）；"
            "③她或他发出的声音、说出的半句话。"
            "器官、体液、液体的流向、体内深处的温度与挤压，全部直接写出名字，不要用「那里」「下面」「部位」「核心」替代。"
            "可以直接用这些词：阴茎、龟头、阴道、阴唇、花核、淫水、爱液、精液、体内深处、抽送、顶弄、收缩、痉挛。"
            "禁止用「快感」「愉悦」「舒服」「燥热」「酥麻」这类概括词——要写出具体是什么感觉、在哪个位置、持续多久。"
            "不要写疼痛、裂开、撕裂、痛呼、痛楚——她只会爽，最多是撑得发酸、太深了发胀。"
        ),
        "avoid": "不写未成年人。",
    },
}


BAD_ENDINGS = ("正文在此", "到此为止", "未完待续", "戛然而止", "故事在此", "本章完")


class LocalWriterPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.conf = config or {}
        self.data_dir = str(StarTools.get_data_dir(NAME))
        os.makedirs(self.data_dir, exist_ok=True)
        self.story_path = os.path.join(self.data_dir, "story.json")
        self.daily_path = os.path.join(self.data_dir, "daily.json")
        self.last_owner_at = 0.0
        self._task: asyncio.Task | None = None
        self._running = False
        self._switch_lock = asyncio.Lock()

    # ---------- 配置 ----------

    def _c(self, key, default=None):
        try:
            val = self.conf.get(key, default)
        except Exception:
            return default
        return default if val is None else val

    def _level(self) -> int:
        try:
            return max(1, min(5, int(self._c("boldness", 5))))
        except (TypeError, ValueError):
            return 5

    def _length(self, given=None) -> int:
        try:
            base = int(given if given is not None else self._c("default_length", 600))
        except (TypeError, ValueError):
            base = 600
        return max(200, min(1500, base))

    def _endpoint(self) -> str:
        return str(self._c("endpoint", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT)

    def _conf_file(self) -> str:
        return os.path.abspath(
            os.path.join(self.data_dir, "..", "..", "config", f"{NAME}_config.json")
        )

    def _preset(self) -> str:
        """以配置文件里的值为准 —— 后台面板改完立刻生效，不用重启。"""
        val = ""
        try:
            with open(self._conf_file(), encoding="utf-8-sig") as f:
                val = str(json.load(f).get("model_preset", "") or "")
        except Exception:  # noqa: BLE001
            val = ""
        if not val:
            val = str(self._c("model_preset", "7b") or "7b")
        val = val.strip().lower()
        return val if val in ("7b", "14b", "auto") else "7b"

    def _model(self) -> str:
        explicit = str(self._c("model", "") or "").strip()
        if explicit:
            return explicit
        key = self._preset()
        if key == "auto":
            return DEFAULT_MODEL
        return PRESETS[key]["model"]

    def _api_base(self) -> str:
        ep = self._endpoint()
        return ep.split("/v1/")[0] if "/v1/" in ep else "http://127.0.0.1:5001"

    async def _current_model(self) -> str:
        url = self._api_base() + "/api/v1/model"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=6)
            ) as sess:
                async with sess.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return str(data.get("result") or "")
        except Exception:  # noqa: BLE001
            return ""
        return ""

    async def _kill_kobold(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/IM", "koboldcpp.exe",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            await asyncio.sleep(3)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 关闭 KoboldCpp 失败: {exc}")

    async def _start_kobold(self, key: str) -> bool:
        cfg = PRESETS[key]
        folder = str(self._c("koboldcpp_dir", r"E:\AI-NSFW-Writer\koboldcpp") or "")
        exe = os.path.join(folder, "koboldcpp.exe")
        model_path = str(self._c(f"model_path_{key}", cfg["path"]) or cfg["path"])
        if not os.path.exists(exe):
            logger.error(f"[local_writer] 找不到 koboldcpp.exe: {exe}")
            return False
        if not os.path.exists(model_path):
            logger.error(f"[local_writer] 找不到模型文件: {model_path}")
            return False
        args = [
            exe, "--model", model_path,
            "--contextsize", str(self._c(f"ctx_{key}", cfg["ctx"])),
            "--gpulayers", str(self._c(f"gl_{key}", cfg["gl"])),
            "--usecublas", "--flashattention", "--quantkv", "1",
            "--host", "127.0.0.1", "--port", "5001", "--skiplauncher", "--quiet",
        ]
        try:
            subprocess.Popen(
                args, cwd=folder,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[local_writer] 启动 KoboldCpp 失败: {exc}")
            return False
        for _ in range(70):
            await asyncio.sleep(3)
            cur = await self._current_model()
            if cfg["tag"] in cur:
                return True
        return False

    async def _ensure_model(self) -> None:
        """配置里的档位和当前加载的不一致时，自动重启 KoboldCpp 换模型。"""
        key = self._preset()
        if key == "auto":
            return
        cfg = PRESETS[key]
        cur = await self._current_model()
        if cfg["tag"] in cur:
            return
        async with self._switch_lock:
            cur = await self._current_model()
            if cfg["tag"] in cur:
                return
            logger.info(f"[local_writer] 切换模型 -> {cfg['tag']}（重启 KoboldCpp）")
            await self._kill_kobold()
            ok = await self._start_kobold(key)
            now = await self._current_model()
            logger.info(
                f"[local_writer] 模型切换{'完成' if ok else '失败'}，当前: {now or '未连接'}"
            )


    def _session(self) -> str:
        explicit = str(self._c("owner_session", "") or "").strip()
        if explicit:
            return explicit
        try:
            cfg = self.context.get_config()
            admins = cfg.get("admins_id") or []
            plats = [
                p for p in (cfg.get("platform") or []) if p.get("type") == "aiocqhttp"
            ]
            if admins and plats:
                return f"{plats[0].get('id')}:FriendMessage:{admins[0]}"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 推导主人会唔失败: {exc}")
        return ""

    # ---------- 连载状态 ----------

    def _load_story(self) -> dict:
        try:
            with open(self.story_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_story(self, story: dict) -> None:
        story["updated"] = int(time.time())
        try:
            with open(self.story_path, "w", encoding="utf-8") as f:
                json.dump(story, f, ensure_ascii=False, indent=1)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[local_writer] 存连载失败: {exc}")

    def _clean_profile(self, text: str) -> str:
        """从人格设定里只取性格与语气，剔掉账号、平台、屏幕这类现实信息。"""
        keep = []
        for line in (text or "").split("\n"):
            s = line.strip()
            if not s:
                continue
            if re.search(r"\d{5,}|QQ|qq|微信|私聊|语音|账号|平台|屏幕|电脑|手机", s):
                continue
            keep.append(s)
        return " ".join(keep)[:300]

    async def _heroine(self) -> dict:
        """女主默认用当前人格。"""
        try:
            cfg = self.context.get_config()
            ar = cfg.get("agent_runner") or {}
            persona_cfg = (ar.get("config") or {}).get("persona") or {}
            pid = persona_cfg.get("persona_id") or ""
            if pid:
                persona = await self.context.persona_manager.get_persona(pid)
                if persona:
                    return {
                        "name": persona.persona_id,
                        "profile": self._clean_profile(persona.system_prompt or ""),
                        "from_persona": True,
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 取当前人格失败: {exc}")
        return {"name": "她", "profile": "", "from_persona": False}

    async def _ensure_story(self) -> dict:
        story = self._load_story()
        if not story.get("heroine"):
            h = await self._heroine()
            story["heroine"] = h
            story.setdefault("title", "")
            story.setdefault("setting", "")
            story.setdefault("chapters", [])
            self._save_story(story)
        return story

    # ---------- 生成 ----------

    def _system_prompt(self, level: int, story: dict) -> str:
        spec = BOLDNESS[level]
        parts = [SYSTEM_BASE, f"【尺度·{spec['name']}】{spec['guide']}"]
        if spec["avoid"]:
            parts.append(f"【红线】{spec['avoid']}")

        tags = str(self._c("custom_tags", "") or "").strip()
        if level >= 5 and tags:
            parts.append(f"【可用元素】{tags}（用得上就用，不要硬塞）")

        heroine = (story or {}).get("heroine") or {}
        if heroine.get("name"):
            block = f"【女主角】她叫{heroine['name']}。"
            if heroine.get("profile"):
                block += f"她的性格与说话方式：{heroine['profile']}"
                block += "\n（保留这个人的性格、语气和小习惯，但故事发生在现实世界，不要出现她原设定里的奇幻身份，除非剧情需要。）"
            parts.append(block)

        return "\n\n".join(parts)

    def _user_prompt(self, instruction: str, length: int, story: dict) -> str:
        parts = []
        story = story or {}
        title = (story.get("title") or "").strip()
        setting = (story.get("setting") or "").strip()
        if title or setting:
            head = title or "未命名"
            parts.append(f"【本篇】{head}" + (f"｜{setting}" if setting else ""))
        chapters = story.get("chapters") or []
        if chapters:
            recent = chapters[-3:]
            lines = [f"第{c.get('idx')}章：{c.get('summary', '')}" for c in recent]
            parts.append("【已写到】\n" + "\n".join(lines))
            tail = recent[-1].get("tail") or ""
            if tail:
                parts.append(f"【上一段结尾】{tail}")
        if (story.get("heroine") or {}).get("name"):
            parts.append(f"【女主】{story['heroine']['name']}（女性，第三人称用「她」）")
        parts.append(f"【这次要写】{instruction}")
        parts.append(f"（正文约 {length} 字，直接开始写）")
        return "\n\n".join(parts)

    async def _chat(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> str:
        payload = {
            "model": self._model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "top_p": 0.95,
            "max_tokens": max_tokens,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._endpoint(), json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"返回结构异常: {str(data)[:200]}") from exc

    async def _generate(
        self, instruction: str, length: int | None = None, level: int | None = None
    ) -> tuple[str, dict]:
        await self._ensure_model()
        story = await self._ensure_story()
        lv = level or self._level()
        ln = self._length(length)
        text = await self._chat(
            self._system_prompt(lv, story),
            self._user_prompt(instruction, ln, story),
            BOLDNESS[lv]["temp"],
            max(400, int(ln * 2.2)),
        )

        # 配了高档位但写得太含蓄 —— 只重试一次
        if lv >= 4 and len(text) < ln * 0.5:
            text = await self._chat(
                self._system_prompt(lv, story),
                self._user_prompt(
                    f"{instruction}\n\n上一版太短太含蓄，这次写长、写具体、不要跳过身体。",
                    ln,
                    story,
                ),
                BOLDNESS[lv]["temp"],
                max(400, int(ln * 2.2)),
            )

        # 结尾跑出正文（"正文到此为止"之类）—— 只重试一次
        tail = text[-100:]
        if any(b in tail for b in BAD_ENDINGS):
            text = await self._chat(
                self._system_prompt(lv, story),
                self._user_prompt(
                    f"{instruction}\n\n最后一句必须停在场景里的动作或对话上，"
                    "不要写任何关于“正文”“篇幅”“到此为止”的话。",
                    ln,
                    story,
                ),
                BOLDNESS[lv]["temp"],
                max(400, int(ln * 2.2)),
            )
            for bad in BAD_ENDINGS:
                if bad in text[-100:]:
                    idx = text.rfind(bad)
                    text = text[:idx].rstrip("。，、 \n")
                    break
        return text, story

    async def _summarize(self, text: str) -> tuple[str, str]:
        """回抽摘要与尾句，存进连载。"""
        tail = ""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if paragraphs:
            last = paragraphs[-1]
            sentences = re.split(r"(?<=[。！？…”」])", last)
            sentences = [s.strip() for s in sentences if s.strip()]
            tail = (sentences[-1] if sentences else last)[-60:]

        summary = ""
        try:
            summary = await self._chat(
                "你是编辑，负责写剧情摘要。",
                f"把下面这段小说压成一句不超过 40 字的剧情摘要，只写发生了什么，不要评价，不要用引号：\n\n{text[:2000]}",
                0.3,
                120,
            )
            summary = summary.strip().strip("。").replace("\n", " ")[:60]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 摘要失败: {exc}")
            summary = (paragraphs[0] if paragraphs else "")[:40]
        return summary, tail

    async def _commit(self, text: str, story: dict) -> None:
        summary, tail = await self._summarize(text)
        chapters = story.setdefault("chapters", [])
        chapters.append(
            {
                "idx": len(chapters) + 1,
                "summary": summary,
                "tail": tail,
                "at": int(time.time()),
            }
        )
        story["chapters"] = chapters[-40:]
        self._save_story(story)
        self._save_article(text, story)

    def _save_article(self, text: str, story: dict) -> None:
        """把正文按篇存一份，方便回看和比对效果。"""
        try:
            folder = os.path.join(self.data_dir, "articles")
            os.makedirs(folder, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + ".md"
            head = story.get("title") or story.get("setting") or "未命名"
            chapter = len(story.get("chapters") or [])
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(f"# {head} · 第 {chapter} 段\n\n{text}\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 存正文失败: {exc}")

    def _split(self, text: str) -> list[str]:
        if len(text) <= MAX_MSG_LEN:
            return [text]
        parts, cur = [], ""
        for para in text.split("\n"):
            if cur and len(cur) + len(para) + 1 > MAX_MSG_LEN:
                parts.append(cur)
                cur = para
            else:
                cur = f"{cur}\n{para}" if cur else para
        if cur:
            parts.append(cur)
        return parts

    # ---------- 语音 ----------

    def _tts_chunks(self, text: str, max_chars: int) -> list[str]:
        """把正文切成适合逐条配音的小段：优先在句末断，长句硬切。"""
        sentences = re.split(r"(?<=[。！？…；\n])", text or "")
        chunks: list[str] = []
        cur = ""
        for raw in sentences:
            s = raw.strip()
            if not s:
                continue
            if len(s) > max_chars:
                if cur:
                    chunks.append(cur)
                    cur = ""
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i : i + max_chars])
                continue
            if cur and len(cur) + len(s) > max_chars:
                chunks.append(cur)
                cur = s
            else:
                cur += s
        if cur:
            chunks.append(cur)
        return [c for c in chunks if c.strip()]

    async def _pick_tts(self):
        """优先用配置里指定的 TTS，否则用当前默认的，再否则取第一个可用的。"""
        wanted = str(self._c("tts_provider_id", "") or "").strip()
        try:
            insts = self.context.get_all_tts_providers() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 取 TTS 列表失败: {exc}")
            insts = []

        if wanted:
            for prov in insts:
                cfg = getattr(prov, "provider_config", {}) or {}
                if cfg.get("id") == wanted:
                    return prov
            logger.warning(f"[local_writer] 没找到 TTS provider {wanted}，改用默认")

        try:
            prov = await self.context.get_using_tts_provider_async(self._session() or None)
            if prov:
                return prov
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 取默认 TTS 失败: {exc}")

        return insts[0] if insts else None

    async def _send_voice(
        self, text: str, target: str
    ) -> tuple[int, str]:
        """逐段配音并发送。返回 (成功发出的条数, 没能发出的剩余文字)。"""
        tts = await self._pick_tts()
        if not tts:
            logger.warning("[local_writer] 没有可用的 TTS provider，改发文字")
            return 0, text

        max_chars = max(40, min(400, int(self._c("tts_max_chars", 120) or 120)))
        max_seg = max(1, min(30, int(self._c("tts_max_segments", 8) or 8)))
        gap = max(0, min(10, int(self._c("tts_gap_seconds", 2) or 0)))
        keep = bool(self._c("tts_keep_audio", False))

        chunks = self._tts_chunks(text, max_chars)
        if not chunks:
            return 0, text

        sent = 0
        for idx, chunk in enumerate(chunks):
            if sent >= max_seg:
                logger.info(f"[local_writer] 语音条数到上限 {max_seg}，剩余转文字")
                return sent, "".join(chunks[idx:])

            try:
                path = await tts.get_audio(chunk)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[local_writer] TTS 第 {idx + 1} 段失败: {exc}")
                return sent, "".join(chunks[idx:])

            if not path or not os.path.exists(path):
                logger.error(f"[local_writer] TTS 第 {idx + 1} 段没产出文件")
                return sent, "".join(chunks[idx:])

            try:
                ok = await self.context.send_message(
                    target, MessageChain(chain=[Record.fromFileSystem(path, text=chunk)])
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[local_writer] 语音发送失败: {exc}")
                ok = False

            if not keep:
                try:
                    os.remove(path)
                except OSError:
                    pass

            if not ok:
                return sent, "".join(chunks[idx:])

            sent += 1
            logger.info(f"[local_writer] 语音 {sent}/{min(len(chunks), max_seg)} 已发（{len(chunk)} 字）")
            if gap and idx < len(chunks) - 1:
                await asyncio.sleep(gap)

        return sent, ""

    # ---------- 投递 ----------

    async def _deliver(
        self, text: str, target: str | None = None, prefix: str | None = None
    ) -> bool:
        """默认发语音；语音发不出去、或配置要求同时发文字时发文字。"""
        if not text:
            return False
        target = target or self._session()
        if not target:
            logger.warning("[local_writer] 没有可用的会话，跳过投递")
            return False

        if prefix:
            try:
                await self.context.send_message(target, MessageChain().message(prefix))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[local_writer] 前缀发送失败: {exc}")

        sent, leftover = 0, text
        if bool(self._c("tts_enable", True)):
            sent, leftover = await self._send_voice(text, target)

        want_text = bool(self._c("tts_send_text", False))
        if leftover or (sent == 0) or want_text:
            body_text = leftover or text
            try:
                for chunk in self._split(body_text):
                    await self.context.send_message(
                        target, MessageChain().message(chunk)
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[local_writer] 文字发送失败: {exc}")
                return sent > 0
        return sent > 0 or bool(text)

    async def _push(self, text: str, prefix: str | None = None) -> bool:
        return await self._deliver(text, None, prefix)

    # ---------- 每日计数 ----------

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _daily(self) -> dict:
        try:
            with open(self.daily_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if data.get("date") != self._today():
            data = {"date": self._today(), "count": 0}
        return data

    def _bump_daily(self) -> None:
        data = self._daily()
        data["count"] = int(data.get("count", 0)) + 1
        try:
            with open(self.daily_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 计数失败: {exc}")

    # ---------- 主动创作 ----------

    def _in_active_hours(self) -> bool:
        span = str(self._c("auto_active_hours", "09:00-24:00") or "").strip()
        try:
            start_s, end_s = span.split("-")
            sh, sm = [int(x) for x in start_s.split(":")]
            eh, em = [int(x) for x in end_s.split(":")]
        except Exception:
            return True
        now = time.localtime()
        cur = now.tm_hour * 60 + now.tm_min
        start = sh * 60 + sm
        end = 24 * 60 if eh == 24 else eh * 60 + em
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end  # 跨零点

    async def _auto_create(self) -> None:
        mode = str(self._c("auto_mode", "continue") or "continue").strip().lower()
        level = self._level()
        story = await self._ensure_story()

        if mode == "new":
            topics = [
                t.strip()
                for t in str(self._c("auto_topics", "") or "").split(",")
                if t.strip()
            ]
            topic = random.choice(topics) if topics else "都市"
            h = await self._heroine()
            story = {
                "title": "",
                "setting": topic,
                "heroine": h,
                "chapters": [],
            }
            self._save_story(story)
            tail_hint = (
                "，然后直接进入明确的性场景，把身体的具体反应写出来"
                if level >= 4
                else "，然后直接进入情节"
            )
            instruction = (
                f"开一个新篇，题材是「{topic}」。用两三句话交代场景和两个人的关系"
                f"{tail_hint}。不要写标题，不要写简介。"
            )
        else:
            if not story.get("chapters"):
                if level >= 4:
                    instruction = (
                        "开篇：两三句话交代场景，以及两个人怎么走到这一步。"
                        "然后由她主动——她先伸手、先贴上去、先说想要——直接进入明确的性场景，"
                        "把身体的具体反应写出来。不要写她犹豫、躲避、抗拒。"
                        "不要写标题，不要写简介。"
                    )
                else:
                    instruction = (
                        "开篇：交代场景和两个人的关系，然后直接进入一个具体的、有张力的瞬间。"
                        "不要写标题，不要写简介。"
                    )
            else:
                instruction = (
                    "接着上一段的结尾往下写，推进一个具体的情节节点。"
                    "不要重复已经发生过的事，不要重新介绍人物。"
                )

        text, story = await self._generate(instruction, None, level)
        if not text:
            logger.warning("[local_writer] 主动创作返回空，跳过")
            return

        ok = await self._push(text, str(self._c("notify_prefix", "") or "").strip() or None)
        if ok:
            self._bump_daily()
            await self._commit(text, story)
            logger.info(f"[local_writer] 主动创作已推送 {len(text)} 字")

    async def _auto_loop(self) -> None:
        await asyncio.sleep(180)  # 等平台与 provider 起稳
        first = True
        while self._running:
            try:
                if first:
                    first = False
                else:
                    mn = int(self._c("auto_min_interval", 120) or 120)
                    mx = int(self._c("auto_max_interval", mn) or mn)
                    mn, mx = max(10, mn), max(10, mx)
                    wait = random.randint(min(mn, mx), max(mn, mx)) * 60
                    await asyncio.sleep(wait)

                if not bool(self._c("auto_enable", False)):
                    continue
                if not self._in_active_hours():
                    logger.info("[local_writer] 不在推送时段，跳过本轮")
                    continue
                limit = int(self._c("auto_daily_limit", 3) or 3)
                if self._daily().get("count", 0) >= limit:
                    logger.info("[local_writer] 今天主动创作已到上限")
                    continue
                skip = int(self._c("auto_skip_if_active", 30) or 0)
                if skip > 0 and time.time() - self.last_owner_at < skip * 60:
                    logger.info("[local_writer] 主人刚才在说话，本轮不打扰")
                    continue

                await self._auto_create()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[local_writer] 主动创作出错: {exc}")
                await asyncio.sleep(300)

    @filter.on_astrbot_loaded()
    async def _boot(self):
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._auto_loop())
        logger.info(
            f"[local_writer] 主动创作调度已启动（enable={self._c('auto_enable', False)}, "
            f"间隔={self._c('auto_min_interval', 120)}-{self._c('auto_max_interval', 120)} 分钟）"
        )

    async def terminate(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[local_writer] 插件已停止，调度任务已取消")

    @filter.on_llm_request()
    async def _mark_active(self, event: AstrMessageEvent, *args, **kwargs):
        self.last_owner_at = time.time()

    # ---------- 工具 ----------

    def _save_config_key(self, key: str, value) -> None:
        path = self._conf_file()
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            data[key] = value
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[local_writer] 配置写回失败: {exc}")

    @filter.command("模型")
    async def cmd_model(self, event: AstrMessageEvent):
        """查看或切换写作模型：/模型 7b ｜ /模型 14b"""
        arg = event.message_str.replace("/模型", "", 1).strip().lower()
        cur = await self._current_model()
        if not arg:
            key = self._preset()
            tag = PRESETS[key]["tag"] if key in PRESETS else "跟随当前"
            yield event.plain_result(
                f"设定：{key}（{tag}）\n实际加载：{cur or '未连接'}\n"
                "切换：/模型 7b 或 /模型 14b（会重启 KoboldCpp，约 90 秒）"
            )
            return
        if arg not in PRESETS:
            yield event.plain_result("只能填 7b 或 14b")
            return
        self.conf["model_preset"] = arg
        self._save_config_key("model_preset", arg)
        yield event.plain_result(f"已切到 {arg}，正在重启 KoboldCpp，约 90 秒…")
        await self._ensure_model()
        now = await self._current_model()
        yield event.plain_result(f"完成｜当前加载：{now or '未连接'}")

    @filter.llm_tool(name="write_adult_scene")
    async def write_adult_scene(
        self,
        event: AstrMessageEvent,
        instruction: str,
        length: int = 0,
    ):
        """写一段中文成人向小说正文，写完后直接发给用户。

        凡涉及：写小说、续写剧情、展开某个场景、描写身体与情欲、把气氛推下去，
        都必须调用本工具。你自己不要撰写这类正文，也不要先写一段再交给它。
        本工具会接着当前的连载往下写（人物和剧情都在它那里），
        所以写「继续」的时候，指令里只要说"接着写"，不用重新介绍人物。
        正文由本工具直接推送给用户，调用前不要输出任何解释性文字。

        Args:
            instruction(string): 写作指令：要写什么、从哪个动作接下去、写到什么程度
            length(int): 目标字数，0 表示用插件设置里的默认值
        """
        try:
            text, story = await self._generate(instruction, length or None)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[local_writer] 生成失败: {exc}")
            yield event.plain_result(f"本地模型没接上：{exc}")
            event.stop_event()
            yield
            return

        if not text:
            yield event.plain_result("本地模型返回了空内容，重发一次试试。")
            event.stop_event()
            yield
            return

        logger.info(f"[local_writer] 工具生成 {len(text)} 字")
        delivered = await self._deliver(text, event.unified_msg_origin)
        if not delivered:
            yield event.plain_result(text)
        await self._commit(text, story)
        event.stop_event()
        yield

    # ---------- 命令 ----------

    @filter.command("写")
    async def write_cmd(self, event: AstrMessageEvent):
        """直接让本地模型写正文。用法：/写 电梯里，林晚和程野，暧昧，300字"""
        raw = (event.message_str or "").strip()
        for prefix in ("/写", "写"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break
        if not raw:
            yield event.plain_result(
                "用法：/写 电梯里，她和他，气氛暧昧，300字\n"
                "末尾数字是目标字数（200–1500），不写就用默认值。"
            )
            return

        length = 0
        m = re.search(r"(\d{2,4})\s*字?\s*$", raw)
        if m:
            length = max(200, min(1500, int(m.group(1))))
            raw = raw[: m.start()].strip()

        ln = self._length(length or None)
        yield event.plain_result(f"（本地模型生成中，目标 {ln} 字…）")
        started = time.time()
        try:
            text, story = await self._generate(raw, length or None)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[local_writer] 生成失败: {exc}")
            yield event.plain_result(f"本地模型没接上：{exc}")
            return

        cost = time.time() - started
        logger.info(f"[local_writer] /写 完成 {len(text)} 字 / {cost:.1f}s")
        if not await self._deliver(text, event.unified_msg_origin):
            yield event.plain_result(text)
        await self._commit(text, story)
        event.stop_event()
        yield

    @filter.command("催更")
    async def urge_cmd(self, event: AstrMessageEvent):
        """催她继续写，立刻写一段，不看间隔和每日上限。"""
        story = await self._ensure_story()
        level = self._level()
        if not story.get("chapters"):
            instruction = (
                "开篇：两三句话交代场景，以及两个人怎么走到这一步。然后由她主动——她先伸手、先贴上去、先说想要——直接进入明确的性场景，把身体的具体反应写出来。不要写她犹豫、躲避、抗拒。"
                if level >= 4
                else "开篇：交代场景和两个人的关系，然后直接进入一个具体的、有张力的瞬间。"
            )
        else:
            instruction = (
                "接着上一段的结尾往下写，只推进一个动作，把这个动作写透，不要跨到下一个动作。她始终是主动迎上来的。"
            )
        yield event.plain_result("（她放下手里的事，接着往下写…）")
        try:
            text, story = await self._generate(instruction)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"没写成：{exc}")
            return
        if not await self._deliver(text, event.unified_msg_origin):
            yield event.plain_result(text)
        await self._commit(text, story)
        event.stop_event()
        yield

    @filter.command("新篇")
    async def new_story_cmd(self, event: AstrMessageEvent):
        """开一篇新的，用法：/新篇 古风"""
        raw = (event.message_str or "").strip()
        for prefix in ("/新篇", "新篇"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break
        topic = raw or "都市"
        h = await self._heroine()
        self._save_story(
            {"title": "", "setting": topic, "heroine": h, "chapters": []}
        )
        instruction = (
            f"开一个新篇，题材是「{topic}」。用两三句话交代场景和两个人的关系"
            + (
                "，然后直接进入明确的性场景，把身体的具体反应写出来"
                if self._level() >= 4
                else "，然后直接进入情节"
            )
            + "。不要写标题，不要写简介。"
        )
        try:
            text, story = await self._generate(instruction)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"没写成：{exc}")
            return
        if not await self._deliver(text, event.unified_msg_origin):
            yield event.plain_result(text)
        await self._commit(text, story)
        event.stop_event()
        yield

    @filter.command("写作")
    async def status_cmd(self, event: AstrMessageEvent):
        """看她现在的写作状态"""
        lv = self._level()
        story = self._load_story()
        chapters = story.get("chapters") or []
        daily = self._daily()
        auto = "开" if self._c("auto_enable", False) else "关"
        lines = [
            f"尺度：{lv} 档·{BOLDNESS[lv]['name']}",
            f"女主：{(story.get('heroine') or {}).get('name') or '未定'}",
            f"连载：{(story.get('title') or '未命名')}｜已写 {len(chapters)} 章",
            f"主动创作：{auto}｜今天已发 {daily.get('count', 0)}/"
            f"{self._c('auto_daily_limit', 3)}",
            f"默认字数：{self._c('default_length', 600)}",
        ]
        if chapters:
            lines.append(f"上一章：{chapters[-1].get('summary', '')}")
        yield event.plain_result("\n".join(lines))
        event.stop_event()
        yield