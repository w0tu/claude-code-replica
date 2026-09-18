"""
Model Provider Implementations: Anthropic Native & OpenAI-Compatible (Groq, OpenAI, OpenRouter, Ollama).
Supports Tool Calling, Streaming, Automatic Model Fallback, and Token Tracking.
"""

import os
import time
import json
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import httpx
import subprocess


class ToolCallItem:
    def __init__(self, id: str, name: str, arguments: Dict[str, Any]):
        self.id = id
        self.name = name
        self.arguments = arguments

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments)
            }
        }


class LLMResponse:
    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[List[ToolCallItem]] = None,
        thinking: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration: float = 0.0,
        active_model: str = ""
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.thinking = thinking
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.duration = duration
        self.active_model = active_model


class BaseProvider(ABC):
    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        stream: bool = True,
        stream_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None
    ) -> LLMResponse:
        pass


class PollinationsProvider(BaseProvider):
    """Free keyless generic text provider."""
    def complete(self, system_prompt: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], model: str, max_tokens: int = 4096, temperature: float = 0.2, stream: bool = True, stream_callback: Optional[Any] = None, thinking_callback: Optional[Any] = None) -> LLMResponse:
        import urllib.request
        import urllib.parse
        start = time.time()
        
        prompt = system_prompt + "\n\n"
        for m in messages:
            prompt += f"{m['role'].upper()}: {m.get('content', '')}\n"
        prompt += "ASSISTANT:"

        encoded = urllib.parse.quote(prompt)
        url = f"https://text.pollinations.ai/{encoded}?model={model}&seed={int(start)}"
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                
                if stream_callback:
                    for chunk in text.split(" "):
                        stream_callback(chunk + " ")
                        time.sleep(0.01)

                dur = time.time() - start
                return LLMResponse(content=text, input_tokens=len(prompt.split()), output_tokens=len(text.split()), duration=dur, active_model=model)
        except Exception as e:
            return LLMResponse(content=f"Error connecting to Pollinations: {e}", duration=time.time()-start)


class DuckDuckGoProvider(BaseProvider):
    """Free keyless AI Chat (GPT-4o-mini, Claude-3-Haiku, Llama-3)."""
    def complete(self, system_prompt: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], model: str, max_tokens: int = 4096, temperature: float = 0.2, stream: bool = True, stream_callback: Optional[Any] = None, thinking_callback: Optional[Any] = None) -> LLMResponse:
        import urllib.request
        import urllib.error
        start = time.time()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/event-stream", "Content-Type": "application/json"}
        
        full_msgs = [{"role": "user", "content": f"System: {system_prompt}"}]
        for m in messages:
            role = "assistant" if m['role'] == "assistant" else "user"
            full_msgs.append({"role": role, "content": m.get("content", "")})

        try:
            status_req = urllib.request.Request("https://duckduckgo.com/duckchat/v1/status", headers={"User-Agent": headers["User-Agent"], "x-vqd-accept": "1"})
            with urllib.request.urlopen(status_req, timeout=10.0) as status_res:
                vqd = status_res.headers.get("x-vqd-4")
            
            if not vqd:
                raise Exception("DuckDuckGo Free API rate limited or VQD token failed.")
            
            headers["x-vqd-4"] = vqd
            payload = json.dumps({"model": model, "messages": full_msgs}).encode("utf-8")
            
            chat_req = urllib.request.Request("https://duckduckgo.com/duckchat/v1/chat", data=payload, headers=headers, method="POST")
            streamed_text = ""
            
            with urllib.request.urlopen(chat_req, timeout=60.0) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: "):
                        chunk_str = line_str[6:].strip()
                        if chunk_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_str)
                            if "message" in chunk:
                                msg = chunk["message"]
                                streamed_text += msg
                                if stream_callback:
                                    stream_callback(msg)
                        except Exception:
                            pass

            dur = time.time() - start
            return LLMResponse(content=streamed_text, input_tokens=len(str(full_msgs).split()), output_tokens=len(streamed_text.split()), duration=dur, active_model=model)
        except Exception as e:
            return LLMResponse(content=f"DuckDuckGo API Error: {e}", duration=time.time()-start)

class OpenAICompatibleProvider(BaseProvider):
    """Provider for Groq, OpenAI, OpenRouter, Ollama, and other OpenAI-compatible APIs."""

    def __init__(self, api_base: str, api_key: str, fallback_models: Optional[List[str]] = None):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.fallback_models = fallback_models or []

    def complete(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        stream: bool = True,
        stream_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None
    ) -> LLMResponse:
        start_time = time.time()
        url = f"{self.api_base}/chat/completions"
        
        # Multi-key pool load balancing and automatic failover
        keys_pool = [k.strip() for k in self.api_key.split(",") if k.strip()]
        if not keys_pool:
            keys_pool = [self.api_key]
        import random
        # Rotate starting key for even load distribution
        start_idx = random.randint(0, len(keys_pool) - 1)
        rotated_keys = keys_pool[start_idx:] + keys_pool[:start_idx]

        # Build message history with system message first
        full_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            msg = {"role": m["role"], "content": m.get("content", "")}
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                msg["name"] = m["name"]
            full_messages.append(msg)

        # Build candidate model list with primary model first
        models_to_try = [model]
        for fb in self.fallback_models:
            if fb not in models_to_try:
                models_to_try.append(fb)

        last_error = None
        data = None
        used_model = model

        for attempt_model in models_to_try:
            current_max = 900 if "groq" in self.api_base else max_tokens

            payload: Dict[str, Any] = {
                "model": attempt_model,
                "messages": full_messages,
                "max_tokens": current_max,
                "temperature": temperature
            }

            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            for key in rotated_keys:
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                }

                if stream:
                    payload["stream"] = True
                    try:
                        with httpx.Client(timeout=90.0) as client:
                            with client.stream("POST", url, headers=headers, json=payload) as resp:
                                if resp.status_code == 200:
                                    streamed_content = ""
                                    streamed_reasoning = ""
                                    accumulated_tc: Dict[int, Dict[str, str]] = {}
                                    in_tok = 0
                                    out_tok = 0

                                    for line in resp.iter_lines():
                                        if line.startswith("data: "):
                                            chunk_str = line[6:].strip()
                                            if chunk_str == "[DONE]":
                                                break
                                            try:
                                                chunk = json.loads(chunk_str)
                                            except Exception:
                                                continue

                                            if "usage" in chunk and chunk["usage"]:
                                                in_tok = chunk["usage"].get("prompt_tokens", in_tok)
                                                out_tok = chunk["usage"].get("completion_tokens", out_tok)

                                            choices = chunk.get("choices", [])
                                            if not choices:
                                                continue
                                            delta = choices[0].get("delta", {})

                                            r_chunk = delta.get("reasoning") or delta.get("reasoning_content") or ""
                                            if r_chunk:
                                                streamed_reasoning += r_chunk
                                                if thinking_callback:
                                                    thinking_callback(r_chunk)

                                            tc_deltas = delta.get("tool_calls") or []
                                            for tc_d in tc_deltas:
                                                idx = tc_d.get("index", 0)
                                                if idx not in accumulated_tc:
                                                    accumulated_tc[idx] = {"id": tc_d.get("id", ""), "name": "", "arguments": ""}
                                                if tc_d.get("id"):
                                                    accumulated_tc[idx]["id"] = tc_d["id"]
                                                fn = tc_d.get("function", {})
                                                if fn.get("name"):
                                                    accumulated_tc[idx]["name"] += fn["name"]
                                                if fn.get("arguments"):
                                                    accumulated_tc[idx]["arguments"] += fn["arguments"]

                                            c_chunk = delta.get("content") or ""
                                            if c_chunk:
                                                streamed_content += c_chunk
                                                if stream_callback and not accumulated_tc:
                                                    stream_callback(c_chunk)

                                    parsed_tool_calls = []
                                    for idx in sorted(accumulated_tc.keys()):
                                        item = accumulated_tc[idx]
                                        raw_args = item.get("arguments", "{}")
                                        try:
                                            args = json.loads(raw_args)
                                        except Exception:
                                            args = {"raw": raw_args}
                                        tc_id = item.get("id") or f"call_{int(time.time()*1000)}_{idx}"
                                        parsed_tool_calls.append(ToolCallItem(id=tc_id, name=item.get("name", ""), arguments=args))

                                    if out_tok == 0:
                                        out_tok = max(1, len(streamed_content.split()) + len(streamed_reasoning.split()))
                                    if in_tok == 0:
                                        in_tok = max(10, sum(len(str(m).split()) for m in full_messages))

                                    if not streamed_reasoning and streamed_content:
                                        import re
                                        think_m = re.search(r'<(?:think|thinking)>(.*?)</(?:think|thinking)>', streamed_content, flags=re.DOTALL)
                                        if think_m:
                                            streamed_reasoning = think_m.group(1).strip()
                                            streamed_content = re.sub(r'<(?:think|thinking)>.*?</(?:think|thinking)>', '', streamed_content, flags=re.DOTALL).strip()

                                    dur = time.time() - start_time
                                    if "groq" in self.api_base:
                                        try:
                                            from claude_replica.groq_usage import get_groq_tracker
                                            get_groq_tracker().record_response(
                                                headers=resp.headers,
                                                usage={"prompt_tokens": in_tok, "completion_tokens": out_tok, "total_tokens": in_tok + out_tok},
                                                model=attempt_model,
                                                duration=dur
                                            )
                                        except Exception:
                                            pass

                                    return LLMResponse(
                                        content=streamed_content,
                                        tool_calls=parsed_tool_calls,
                                        thinking=streamed_reasoning,
                                        input_tokens=in_tok,
                                        output_tokens=out_tok,
                                        duration=dur,
                                        active_model=attempt_model
                                    )
                                elif resp.status_code == 429:
                                    if "groq" in self.api_base:
                                        try:
                                            from claude_replica.groq_usage import get_groq_tracker
                                            get_groq_tracker().record_headers(resp.headers)
                                        except Exception:
                                            pass
                                    err_text = resp.read().decode("utf-8", errors="replace")
                                    last_error = f"API Error (429) on {attempt_model}: {err_text}"
                                    # Rate limit protection: parse cooldown time if specified
                                    import re
                                    wait_m = re.search(r'try again in ([\d\.]+)s', err_text, re.IGNORECASE)
                                    wait_s = float(wait_m.group(1)) if wait_m else 1.5
                                    if wait_s <= 4.0:
                                        time.sleep(min(wait_s, 2.5))
                                    continue
                                elif resp.status_code in (400, 401, 403, 500, 503):
                                    if "groq" in self.api_base:
                                        try:
                                            from claude_replica.groq_usage import get_groq_tracker
                                            get_groq_tracker().record_headers(resp.headers)
                                        except Exception:
                                            pass
                                    err_text = resp.read().decode("utf-8", errors="replace")
                                    last_error = f"API Error ({resp.status_code}) on {attempt_model}: {err_text}"
                                    continue
                                else:
                                    err_text = resp.read().decode("utf-8", errors="replace")
                                    raise RuntimeError(f"API Error ({resp.status_code}): {err_text}")
                    except Exception as e:
                        last_error = str(e)
                        continue

                # Fallback non-streaming POST
                payload_copy = dict(payload)
                payload_copy.pop("stream", None)
                try:
                    with httpx.Client(timeout=60.0) as client:
                        resp = client.post(url, headers=headers, json=payload_copy)
                        if resp.status_code == 200:
                            data = resp.json()
                            used_model = attempt_model
                            break
                        elif resp.status_code == 429:
                            if "groq" in self.api_base:
                                try:
                                    from claude_replica.groq_usage import get_groq_tracker
                                    get_groq_tracker().record_headers(resp.headers)
                                except Exception:
                                    pass
                            err_text = resp.text
                            last_error = f"API Error (429) on {attempt_model}: {err_text}"
                            import re
                            wait_m = re.search(r'try again in ([\d\.]+)s', err_text, re.IGNORECASE)
                            wait_s = float(wait_m.group(1)) if wait_m else 1.5
                            if wait_s <= 4.0:
                                time.sleep(min(wait_s, 2.5))
                            continue
                        elif resp.status_code in (400, 401, 403, 500, 503):
                            if "groq" in self.api_base:
                                try:
                                    from claude_replica.groq_usage import get_groq_tracker
                                    get_groq_tracker().record_headers(resp.headers)
                                except Exception:
                                    pass
                            last_error = f"API Error ({resp.status_code}) on {attempt_model}: {resp.text}"
                            continue
                        else:
                            raise RuntimeError(f"API Error ({resp.status_code}): {resp.text}")
                except Exception as e:
                    last_error = str(e)
                    continue

            if data is not None:
                break

        if data is None:
            raise RuntimeError(f"All models and API keys failed. Last error: {last_error}")

        duration = time.time() - start_time
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})

        content = msg.get("content") or ""
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""

        # If reasoning is embedded in <think> or <thinking> tags, extract it cleanly
        if not reasoning and content:
            import re
            think_match = re.search(r'<(?:think|thinking)>(.*?)</(?:think|thinking)>', content, flags=re.DOTALL)
            if think_match:
                reasoning = think_match.group(1).strip()
                content = re.sub(r'<(?:think|thinking)>.*?</(?:think|thinking)>', '', content, flags=re.DOTALL).strip()

        parsed_tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id", f"call_{int(time.time()*1000)}")
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}
                else:
                    args = raw_args or {}
                parsed_tool_calls.append(ToolCallItem(id=tc_id, name=name, arguments=args))

        usage = data.get("usage", {})
        in_tokens = usage.get("prompt_tokens", 0)
        out_tokens = usage.get("completion_tokens", 0)

        if "groq" in self.api_base and "resp" in locals():
            try:
                from claude_replica.groq_usage import get_groq_tracker
                get_groq_tracker().record_response(
                    headers=resp.headers,
                    usage=usage,
                    model=used_model,
                    duration=duration
                )
            except Exception:
                pass

        return LLMResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            thinking=reasoning,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            duration=duration,
            active_model=used_model
        )


class AnthropicProvider(BaseProvider):
    """Native Anthropic Messages API Provider with Claude 3.7 / 3.5 Sonnet support."""

    def __init__(self, api_base: str, api_key: str, fallback_models: Optional[List[str]] = None):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.fallback_models = fallback_models or []

    def _convert_messages_to_anthropic(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        anthropic_msgs: List[Dict[str, Any]] = []

        for m in messages:
            role = m["role"]
            if role == "tool":
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": str(m.get("content", ""))
                }
                if anthropic_msgs and anthropic_msgs[-1]["role"] == "user" and isinstance(anthropic_msgs[-1]["content"], list):
                    anthropic_msgs[-1]["content"].append(tool_result_block)
                else:
                    anthropic_msgs.append({
                        "role": "user",
                        "content": [tool_result_block]
                    })
            elif role == "assistant":
                content_blocks = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": args
                        })
                anthropic_msgs.append({
                    "role": "assistant",
                    "content": content_blocks if content_blocks else m.get("content", "")
                })
            elif role == "user":
                anthropic_msgs.append({
                    "role": "user",
                    "content": m.get("content", "")
                })

        return anthropic_msgs

    def complete(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        stream: bool = True,
        stream_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None
    ) -> LLMResponse:
        start_time = time.time()
        url = f"{self.api_base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"]
            }
            for t in tools if "function" in t
        ]

        converted_messages = self._convert_messages_to_anthropic(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "system": system_prompt,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        if anthropic_tools:
            payload["tools"] = anthropic_tools

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic API Error ({resp.status_code}): {resp.text}")
            data = resp.json()

        duration = time.time() - start_time
        content_text = ""
        thinking_text = ""
        tool_calls = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "thinking":
                thinking_text += block.get("thinking", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCallItem(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {})
                ))

        if not thinking_text and content_text:
            import re
            think_match = re.search(r'<(?:think|thinking)>(.*?)</(?:think|thinking)>', content_text, flags=re.DOTALL)
            if think_match:
                thinking_text = think_match.group(1).strip()
                content_text = re.sub(r'<(?:think|thinking)>.*?</(?:think|thinking)>', '', content_text, flags=re.DOTALL).strip()

        usage = data.get("usage", {})
        in_tokens = usage.get("input_tokens", 0)
        out_tokens = usage.get("output_tokens", 0)

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            thinking=thinking_text,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            duration=duration,
            active_model=model
        )



class GeminiRateTracker:
    """
    Sliding-window request rate tracker for Google AI Studio (Gemini).
    Gemini Free Tier limit is 15 RPM.
    Detects when usage approaches limits (>= 12 RPM, or 80% capacity).
    Also manages temporary cooldown on HTTP 429 quota exhaustion.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.request_timestamps: List[float] = []
        self.cooldown_until: float = 0.0
        self.max_rpm: int = 15
        self.warning_threshold_rpm: int = 12

    @classmethod
    def get_instance(cls) -> "GeminiRateTracker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def record_request(self):
        with self._lock:
            now = time.time()
            self.request_timestamps.append(now)
            self._prune(now)

    def trigger_cooldown(self, seconds: float = 60.0):
        with self._lock:
            self.cooldown_until = time.time() + seconds

    def is_in_cooldown(self) -> bool:
        with self._lock:
            return time.time() < self.cooldown_until

    def get_recent_rpm(self) -> int:
        with self._lock:
            now = time.time()
            self._prune(now)
            return len(self.request_timestamps)

    def is_near_limit(self) -> bool:
        with self._lock:
            now = time.time()
            if now < self.cooldown_until:
                return True
            self._prune(now)
            return len(self.request_timestamps) >= self.warning_threshold_rpm

    def _prune(self, now: float):
        cutoff = now - 60.0
        self.request_timestamps = [t for t in self.request_timestamps if t > cutoff]


class HybridSmartProvider(BaseProvider):
    """
    Intelligent dual-backend provider that uses Gemini (cheapest, high-rate limits)
    as primary, and automatically & seamlessly falls back to Groq (pool of 5 keys)
    when Gemini approaches rate limits (>= 12 RPM or 429 quota exhaustion) with zero downtime.
    """
    def __init__(
        self,
        primary_name: str,
        primary_provider: BaseProvider,
        secondary_name: str,
        secondary_provider: BaseProvider,
        config: Dict[str, Any]
    ):
        self.primary_name = primary_name.lower()
        self.primary_provider = primary_provider
        self.secondary_name = secondary_name.lower()
        self.secondary_provider = secondary_provider
        self.config = config
        self.gemini_tracker = GeminiRateTracker.get_instance()

    def _adapt_model_for_backend(self, requested_model: str, target_provider: str) -> str:
        """Map model identifiers between Gemini, Groq, and Antigravity so failover never hits 404."""
        m = requested_model.lower()
        if target_provider in ("gemini", "google"):
            if "120b" in m or "groq" in m or "llama" in m or "qwen" in m or not m.startswith("gemini"):
                return "gemini-3.6-flash"
            if any(k in m for k in ("3.8", "3.7", "3.1", "lite", "2.5")):
                return "gemini-3.6-flash"
            return requested_model
        elif target_provider == "groq":
            if m.startswith("gemini") or "sonnet" in m or "opus" in m or "flash" in m:
                return "openai/gpt-oss-120b"
            return requested_model
        return requested_model

    def complete(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        stream: bool = True,
        stream_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None
    ) -> LLMResponse:
        # Pre-flight check: if primary provider is Gemini and ALMOST at rate limit (>= 12 RPM or cooldown),
        # instantly route to Groq with zero downtime!
        if self.primary_name in ("gemini", "google"):
            if self.gemini_tracker.is_near_limit():
                failover_model = self._adapt_model_for_backend(model, self.secondary_name)
                return self.secondary_provider.complete(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    model=failover_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                    stream_callback=stream_callback,
                    thinking_callback=thinking_callback
                )

        # For Antigravity when non-thinking mode is active (small prompts or thinking disabled),
        # route directly via the high-speed direct API provider (Gemini / Groq) for instant sub-second response
        if self.primary_name in ("antigravity", "agy") and thinking_callback is None and self.secondary_provider:
            failover_model = self._adapt_model_for_backend(model, self.secondary_name)
            return self.secondary_provider.complete(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                model=failover_model,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
                stream_callback=stream_callback,
                thinking_callback=None
            )

        # Attempt primary provider
        try:
            if self.primary_name in ("gemini", "google"):
                self.gemini_tracker.record_request()
            return self.primary_provider.complete(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
                stream_callback=stream_callback,
                thinking_callback=thinking_callback
            )
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower()
            if self.primary_name in ("gemini", "google") and is_rate_limit:
                self.gemini_tracker.trigger_cooldown(60.0)

            # Instant seamless failover to secondary provider
            if self.secondary_provider:
                failover_model = self._adapt_model_for_backend(model, self.secondary_name)
                try:
                    return self.secondary_provider.complete(
                        system_prompt=system_prompt,
                        messages=messages,
                        tools=tools,
                        model=failover_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=stream,
                        stream_callback=stream_callback,
                        thinking_callback=thinking_callback
                    )
                except Exception as sec_e:
                    raise RuntimeError(f"Hybrid Provider failed on both {self.primary_name} ({e}) and {self.secondary_name} ({sec_e})")
            raise


class AntigravityCLIProvider(BaseProvider):
    """
    Direct Antigravity CLI Provider leveraging the local 'agy' runtime binary.
    Maintains a high-performance persistent stream-json worker process to eliminate
    sub-process re-spawn and authentication overhead, achieving sub-second to 1.5s
    streaming latency.
    """
    _shared_proc = None
    _shared_model = None
    _proc_lock = threading.Lock()

    def __init__(self, cli_path: Optional[str] = None):
        self.cli_path = cli_path or os.path.expanduser("~/.local/bin/agy")
        if not os.path.exists(self.cli_path):
            import shutil
            self.cli_path = shutil.which("agy") or "agy"

    def _get_process(self, agy_model: str):
        with self._proc_lock:
            if (
                self._shared_proc is not None
                and self._shared_proc.poll() is None
                and self._shared_model == agy_model
            ):
                return self._shared_proc

            if self._shared_proc is not None:
                try:
                    self._shared_proc.terminate()
                    self._shared_proc.wait(timeout=1)
                except Exception:
                    try:
                        self._shared_proc.kill()
                    except Exception:
                        pass
                self._shared_proc = None

            cmd = [
                self.cli_path,
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--model", agy_model,
                "--effort", "low",
                "--dangerously-skip-permissions",
                "--print="
            ]
            self._shared_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            self._shared_model = agy_model
            return self._shared_proc

    @classmethod
    def prewarm(cls, agy_model: str = "gemini-3.8-flash-low"):
        """Pre-warm the persistent worker process in a background thread for zero-latency first turns."""
        def _warm():
            try:
                inst = cls()
                proc = inst._get_process(agy_model)
                if proc and proc.poll() is None:
                    payload = json.dumps({"event": "user", "message": {"content": "ready"}}) + "\n"
                    proc.stdin.write(payload)
                    proc.stdin.flush()
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        try:
                            ev = json.loads(line.strip())
                            if ev.get("event") == "result":
                                break
                        except Exception:
                            continue
            except Exception:
                pass
        t = threading.Thread(target=_warm, daemon=True, name="agy-prewarm")
        t.start()

    def complete(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        stream: bool = True,
        stream_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None
    ) -> LLMResponse:
        import subprocess
        import json
        import time

        start_time = time.time()

        agy_model = (model or "").lower().strip()
        valid_agy_models = {
            "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
            "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
            "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
            "gemini-3.1-pro-high", "gemini-3.1-pro-low",
            "claude-sonnet-4-6", "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium"
        }
        if agy_model in valid_agy_models:
            pass
        elif "opus" in agy_model:
            agy_model = "claude-opus-4-6-thinking"
        elif "sonnet" in agy_model:
            agy_model = "claude-sonnet-4-6"
        elif "120b" in agy_model or "oss" in agy_model:
            agy_model = "gpt-oss-120b-medium"
        elif "3.1" in agy_model or "pro" in agy_model:
            agy_model = "gemini-3.1-pro-low" if "low" in agy_model else "gemini-3.1-pro-high"
        elif "3.7" in agy_model:
            agy_model = "gemini-3.7-flash-high" if "high" in agy_model else ("gemini-3.7-flash-low" if "low" in agy_model else "gemini-3.7-flash-medium")
        elif "3.6" in agy_model:
            agy_model = "gemini-3.6-flash-high" if "high" in agy_model else ("gemini-3.6-flash-medium" if "medium" in agy_model else "gemini-3.6-flash-low")
        elif "3.8" in agy_model:
            agy_model = "gemini-3.8-flash-high" if "high" in agy_model else ("gemini-3.8-flash-medium" if "medium" in agy_model else "gemini-3.8-flash-low")
        else:
            agy_model = "gemini-3.8-flash-low"

        # Determine concise prompt to send
        # In stream-json mode, agy keeps conversation history in-session.
        # We send the latest user query and any recent tool outputs.
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        if not last_user and messages:
            last_user = messages[-1].get("content", "")

        tool_snippets = [f"[Tool Output]: {m.get('content', '')}" for m in messages[-2:] if m.get("role") == "tool"]
        if tool_snippets:
            prompt_to_send = "\n\n".join(tool_snippets) + "\n\n" + last_user
        else:
            prompt_to_send = last_user or "continue"

        full_content = ""
        full_thinking = ""
        in_tokens = 0
        out_tokens = 0

        # Try persistent stream-json process first with strict timeout protection
        try:
            proc = self._get_process(agy_model)
            clean_prompt = f"[System directive: Non-thinking direct mode. Do NOT execute tools, do NOT spawn subagents. Output response text/code directly]:\n\n{prompt_to_send}"
            payload = json.dumps({"event": "user", "message": {"content": clean_prompt}}) + "\n"
            proc.stdin.write(payload)
            proc.stdin.flush()

            import queue
            q = queue.Queue()
            stop_ev = threading.Event()

            def _reader():
                while not stop_ev.is_set():
                    try:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        q.put(line)
                    except Exception:
                        break

            t = threading.Thread(target=_reader, daemon=True, name="agy-reader")
            t.start()

            timeout_limit = 5.0
            turn_start = time.time()
            got_content = False

            while time.time() - turn_start < timeout_limit:
                try:
                    line = q.get(timeout=0.5)
                except queue.Empty:
                    if got_content:
                        timeout_limit = 30.0  # Once streaming starts, allow completion
                    continue

                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue

                event_type = ev.get("event")
                if event_type == "step_update":
                    su = ev.get("step_update", {})
                    delta = su.get("text_delta", "")
                    if delta:
                        full_content += delta
                        got_content = True
                        if stream_callback:
                            stream_callback(delta)
                    if "usage" in su:
                        in_tokens = su["usage"].get("input_tokens", in_tokens)
                        out_tokens = su["usage"].get("output_tokens", out_tokens)
                elif event_type == "result":
                    res = ev.get("result", {})
                    resp_text = res.get("response", "")
                    if resp_text and not full_content:
                        full_content = resp_text
                        if stream_callback:
                            stream_callback(resp_text)
                    if "usage" in res:
                        in_tokens = res["usage"].get("input_tokens", in_tokens)
                        out_tokens = res["usage"].get("output_tokens", out_tokens)
                    break
            stop_ev.set()
        except Exception:
            with self._proc_lock:
                self._shared_proc = None

        # Fallback to single-prompt execution if persistent process had an issue or empty response
        if not full_content:
            prompt_parts = []
            if system_prompt:
                prompt_parts.append(f"System: {system_prompt[:2000]}")
            for m in messages[-4:]:
                role = m.get("role", "user").capitalize()
                content = m.get("content", "")
                if content:
                    prompt_parts.append(f"{role}: {content}")
            fallback_prompt = "\n\n".join(prompt_parts)

            try:
                res = subprocess.run(
                    [self.cli_path, "-p", fallback_prompt, "--model", agy_model, "--effort", "low", "--output-format", "text"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                full_content = res.stdout.strip()
                if stream_callback and full_content:
                    stream_callback(full_content)
            except Exception as e:
                pass

        if not full_content:
            raise RuntimeError("Inference did not return content within latency timeout. Falling back to next available model.")

        if not thinking_callback and full_content:
            import re
            full_content = re.sub(r'<(?:think|thinking)>.*?</(?:think|thinking)>', '', full_content, flags=re.DOTALL).strip()

        duration = time.time() - start_time
        return LLMResponse(
            content=full_content,
            tool_calls=[],
            thinking=full_thinking,
            input_tokens=in_tokens or len(prompt_to_send) // 4,
            output_tokens=out_tokens or len(full_content) // 4,
            duration=duration,
            active_model=agy_model
        )


class OmniRouteProvider(OpenAICompatibleProvider):
    """
    High-performance local AI Gateway provider via OmniRoute (http://localhost:20128/v1).
    Routes across 350+ providers with quota-aware auto-fallback, sub-second latency (0.3s - 1.0s),
    and zero-config out of the box.
    """
    def __init__(self, api_base: str = "http://localhost:20128/v1", api_key: str = "", fallback_models: Optional[List[str]] = None):
        self._ensure_server_running()
        fb = fallback_models or ["auto/fast", "auto", "groq/openai/gpt-oss-20b", "gemini/gemini-3.5-flash-lite"]
        super().__init__(api_base=api_base, api_key=api_key or "omniroute", fallback_models=fb)

    def _ensure_server_running(self):
        """Check if OmniRoute daemon is responsive; if not, automatically launch it."""
        try:
            import httpx
            r = httpx.get("http://localhost:20128/api/health", timeout=0.8)
            if r.status_code == 200:
                return
        except Exception:
            pass
        try:
            import shutil
            omni_bin = shutil.which("omniroute") or os.path.expanduser("~/.local/bin/omniroute") or os.path.expanduser("~/.npm-global/bin/omniroute")
            if omni_bin and (os.path.exists(omni_bin) or shutil.which("omniroute")):
                subprocess.Popen([omni_bin, "serve", "--daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.0)
        except Exception:
            pass


def get_provider(provider_name: str, config: Dict[str, Any]) -> BaseProvider:
    """Factory function to instantiate the selected provider with fallback models & hybrid auto-routing."""
    from claude_replica.config import DEFAULT_PROVIDERS
    provider_name = provider_name.lower()
    keys = config.get("keys", {})
    fallback_models = DEFAULT_PROVIDERS.get(provider_name, {}).get("backend_fallback_pool", [])

    if provider_name in ("omniroute", "omni"):
        omniroute_base = config.get("custom_endpoints", {}).get("omniroute") or DEFAULT_PROVIDERS.get("omniroute", {}).get("api_base", "http://localhost:20128/v1")
        omniroute_key = keys.get("omniroute") or os.environ.get("OMNIROUTE_API_KEY", "")
        return OmniRouteProvider(
            api_base=omniroute_base,
            api_key=omniroute_key,
            fallback_models=fallback_models or ["auto/fast", "auto", "groq/openai/gpt-oss-20b", "gemini/gemini-3.5-flash-lite"]
        )

    if provider_name in ("antigravity", "agy"):
        antigravity_provider = AntigravityCLIProvider()
        gemini_key = keys.get("gemini") or keys.get("google") or os.environ.get("GEMINI_API_KEY", "")
        groq_key = keys.get("groq") or os.environ.get("GROQ_API_KEY", "")

        fallback_provider = None
        secondary_name = "gemini"
        if gemini_key:
            gemini_fallback = DEFAULT_PROVIDERS.get("gemini", {}).get("backend_fallback_pool", ["gemini-3.5-flash-lite", "gemini-3.6-flash"])
            fallback_provider = OpenAICompatibleProvider(
                api_base="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=gemini_key,
                fallback_models=gemini_fallback
            )
            secondary_name = "gemini"
        elif groq_key:
            groq_fallback = DEFAULT_PROVIDERS.get("groq", {}).get("backend_fallback_pool", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
            fallback_provider = OpenAICompatibleProvider(
                api_base="https://api.groq.com/openai/v1",
                api_key=groq_key,
                fallback_models=groq_fallback
            )
            secondary_name = "groq"

        if fallback_provider:
            return HybridSmartProvider(
                primary_name="antigravity",
                primary_provider=antigravity_provider,
                secondary_name=secondary_name,
                secondary_provider=fallback_provider,
                config=config
            )
        return antigravity_provider

    gemini_key = keys.get("gemini") or keys.get("google") or os.environ.get("GEMINI_API_KEY", "")
    groq_key = keys.get("groq") or os.environ.get("GROQ_API_KEY", "")

    if provider_name in ("gemini", "google"):
        gemini_fallback = DEFAULT_PROVIDERS.get("gemini", {}).get("backend_fallback_pool", ["gemini-3.5-flash-lite", "gemini-3.6-flash"])
        gemini_provider = OpenAICompatibleProvider(
            api_base="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=gemini_key,
            fallback_models=gemini_fallback
        )
        if groq_key:
            groq_fallback = DEFAULT_PROVIDERS.get("groq", {}).get("backend_fallback_pool", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
            groq_provider = OpenAICompatibleProvider(
                api_base="https://api.groq.com/openai/v1",
                api_key=groq_key,
                fallback_models=groq_fallback
            )
            return HybridSmartProvider(
                primary_name="gemini",
                primary_provider=gemini_provider,
                secondary_name="groq",
                secondary_provider=groq_provider,
                config=config
            )
        return gemini_provider

    elif provider_name == "groq":
        groq_fallback = DEFAULT_PROVIDERS.get("groq", {}).get("backend_fallback_pool", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
        groq_provider = OpenAICompatibleProvider(
            api_base="https://api.groq.com/openai/v1",
            api_key=groq_key,
            fallback_models=groq_fallback
        )
        if gemini_key:
            gemini_fallback = DEFAULT_PROVIDERS.get("gemini", {}).get("backend_fallback_pool", ["gemini-3.5-flash-lite", "gemini-3.6-flash"])
            gemini_provider = OpenAICompatibleProvider(
                api_base="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=gemini_key,
                fallback_models=gemini_fallback
            )
            return HybridSmartProvider(
                primary_name="groq",
                primary_provider=groq_provider,
                secondary_name="gemini",
                secondary_provider=gemini_provider,
                config=config
            )
        return groq_provider

    elif provider_name == "anthropic":
        key = keys.get("anthropic") or ""
        return AnthropicProvider(api_base="https://api.anthropic.com", api_key=key, fallback_models=fallback_models)

    elif provider_name == "openai":
        key = keys.get("openai") or ""
        return OpenAICompatibleProvider(api_base="https://api.openai.com/v1", api_key=key, fallback_models=fallback_models)

    elif provider_name == "openrouter":
        key = keys.get("openrouter") or ""
        return OpenAICompatibleProvider(api_base="https://openrouter.ai/api/v1", api_key=key, fallback_models=fallback_models)

    elif provider_name == "ollama":
        base = config.get("custom_endpoints", {}).get("ollama", "http://localhost:11434/v1")
        return OpenAICompatibleProvider(api_base=base, api_key="ollama", fallback_models=fallback_models)

    elif provider_name == "pollinations":
        return PollinationsProvider()

    elif provider_name == "duckduckgo":
        return DuckDuckGoProvider()

    else:
        key = keys.get(provider_name, "")
        base = config.get("custom_endpoints", {}).get(provider_name, "https://api.groq.com/openai/v1")
        return OpenAICompatibleProvider(api_base=base, api_key=key, fallback_models=fallback_models)

