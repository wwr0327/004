"""Standard-library client for DeepSeek's OpenAI-compatible Chat API."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ENV_KEYS = {"DEEPSEEK_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_THINKING",
            "LLM_PROVIDER", "OLLAMA_API_KEY"}


def load_env_file(path=".env"):
    """Load recognized settings from a small local .env without overriding the shell."""
    env_path = Path(path)
    try:
        if env_path.stat().st_size > 16 * 1024:
            raise ValueError(".env 文件过大")
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f".env 第 {number} 行缺少等号")
        name, value = (part.strip() for part in line.split("=", 1))
        if name not in ENV_KEYS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


class ModelError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatClient:
    def __init__(self, api_key, base_url, model, thinking="disabled", timeout=60,
                 retries=2, opener=None, sleep=time.sleep):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("LLM_BASE_URL 必须是无认证信息、查询参数或片段的 HTTP(S) 地址")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程模型服务必须使用 HTTPS")
        if not api_key.strip() or not model.strip():
            raise ValueError("请配置所选服务的 API Key 和模型；无 Key 时请使用 --demo")
        thinking = thinking.strip().lower()
        if thinking not in {"enabled", "disabled"}:
            raise ValueError("LLM_THINKING 只能是 enabled 或 disabled")
        self.api_key, self.model = api_key, model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.is_deepseek = parsed.hostname == "api.deepseek.com" or parsed.hostname.endswith(".deepseek.com")
        self.thinking = thinking
        self.timeout, self.retries = timeout, retries
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep = sleep

    @classmethod
    def from_env(cls, provider=None, model=None, base_url=None, thinking=None, discovery=False):
        load_env_file()
        legacy_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        inferred = "deepseek" if urllib.parse.urlsplit(legacy_url).hostname == "api.deepseek.com" else "custom"
        configured = os.getenv("LLM_PROVIDER", inferred)
        selected = provider or configured
        if selected not in {"deepseek", "custom", "ollama"}:
            raise ValueError("LLM_PROVIDER 必须是 deepseek、custom 或 ollama")
        same_profile = selected == configured
        defaults = {"deepseek": "https://api.deepseek.com", "ollama": "http://localhost:11434/v1"}
        url = base_url or (os.getenv("LLM_BASE_URL") if same_profile else None) or defaults.get(selected)
        if not url:
            raise ValueError("custom 服务需要 --base-url 或 LLM_BASE_URL")
        host = urllib.parse.urlsplit(url).hostname
        # Never forward a provider-specific credential to a different service.
        if selected == "deepseek":
            if host != "api.deepseek.com":
                raise ValueError("DeepSeek 配置只连接 api.deepseek.com；其他地址请选择 custom 并设置 LLM_API_KEY")
            key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", "")
        elif selected == "ollama":
            key = os.getenv("OLLAMA_API_KEY", "")
            if not key and host in {"localhost", "127.0.0.1", "::1"}:
                key = "ollama"
        else:
            key = os.getenv("LLM_API_KEY", "")
        if not key.strip():
            key_name = {"deepseek": "DEEPSEEK_API_KEY", "custom": "LLM_API_KEY", "ollama": "OLLAMA_API_KEY"}[selected]
            raise ValueError(f"请设置 {key_name}；不会使用其他服务的专用密钥")
        selected_model = model or (os.getenv("LLM_MODEL") if same_profile else None)
        selected_model = selected_model or ("deepseek-flash" if selected == "deepseek" else "")
        if not selected_model and not discovery:
            raise ValueError("请用 --model 指定模型，或用 --select-model 从服务中选择")
        client = cls(key, url, selected_model or "discovery", thinking or os.getenv("LLM_THINKING", "disabled"))
        client.provider = selected
        return client

    def list_models(self):
        request = urllib.request.Request(self.url.removesuffix("/chat/completions") + "/models",
                                         headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ModelError("模型列表过大")
            payload = json.loads(raw)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or any(not isinstance(item, dict) or
                    not isinstance(item.get("id"), str) or not item["id"].strip() for item in data):
                raise ValueError("invalid models")
            return sorted({item["id"] for item in data})
        except urllib.error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            code = exc.code
            exc.close()
            raise ModelError(f"读取模型列表失败 HTTP {code}：{detail or '该服务可能不支持 /models，请使用 --model'}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise ModelError("读取模型列表连接失败，请检查地址及服务是否启动") from None
        except (ValueError, TypeError):
            raise ModelError("模型列表格式无效，需要 data[].id") from None

    def _http_error_detail(self, exc):
        try:
            raw = exc.read(16 * 1024 + 1)
            if not raw or len(raw) > 16 * 1024:
                return ""
            payload = json.loads(raw)
            error = payload.get("error") if isinstance(payload, dict) else None
            detail = error.get("message", "") if isinstance(error, dict) else ""
            if not isinstance(detail, str):
                return ""
            detail = " ".join(detail.split()).replace(self.api_key, "[REDACTED]")
            return detail[:500]
        except (AttributeError, UnicodeDecodeError, ValueError, TypeError):
            return ""

    def complete(self, messages, tools):
        request_body = {"model": self.model, "messages": messages}
        if tools:
            request_body.update(tools=tools, tool_choice="auto")
        # DeepSeek enables thinking by default. Make the selected mode explicit so
        # tool-call continuation behavior remains stable across API releases.
        if self.is_deepseek:
            request_body["thinking"] = {"type": self.thinking}
        payload = json.dumps(request_body).encode("utf-8")
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.url, data=payload, headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ModelError("模型响应过大")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("invalid response")
                choice = result["choices"][0]
                if not isinstance(choice, dict):
                    raise ValueError("invalid choice")
                if choice.get("finish_reason") in {"length", "content_filter"}:
                    raise ModelError("模型响应被截断或过滤，请缩小问题范围")
                message = choice["message"]
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise ValueError("invalid message")
                # DeepSeek requires reasoning_content to be sent back verbatim on
                # later requests when thinking mode and tools are used.
                return {k: message[k] for k in ("role", "content", "reasoning_content", "tool_calls")
                        if k in message}
            except urllib.error.HTTPError as exc:
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
                detail = self._http_error_detail(exc)
                exc.close()
                if not retryable or attempt == self.retries:
                    suffix = f"：{detail}" if detail else "，请检查配置、额度或服务状态"
                    raise ModelError(f"模型服务返回 HTTP {exc.code}{suffix}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise ModelError("模型网络请求失败或超时，请检查网络和服务地址") from None
            except (ValueError, KeyError, IndexError, TypeError):
                raise ModelError("模型响应格式无效，需要支持工具调用的 Chat Completions 接口") from None
            self.sleep(min(2 ** attempt, 8))
