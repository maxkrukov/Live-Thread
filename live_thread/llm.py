"""OpenAI-compatible /v1/chat/completions client (vLLM, sglang, llama.cpp, Ollama, OpenAI, etc.)."""
import json
import urllib.request


class LLM:
    def __init__(self, settings):
        self.settings = settings

    def _request(self, messages, max_tokens, stream, temperature=0.1):
        s = self.settings
        body = {
            "model": s["llm_model"] or "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            # Disable reasoning traces for models that support it (e.g. Qwen3); other servers ignore this.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {"Content-Type": "application/json"}
        if s.get("llm_api_key"):
            headers["Authorization"] = f"Bearer {s['llm_api_key']}"
        return urllib.request.Request(s["llm_url"], data=json.dumps(body).encode(), headers=headers)

    def complete(self, messages, max_tokens=300):
        with urllib.request.urlopen(self._request(messages, max_tokens, False), timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""

    def stream_chunks(self, messages, max_tokens=300):
        """Yield incremental text deltas as they arrive from a streaming response."""
        with urllib.request.urlopen(self._request(messages, max_tokens, True), timeout=60) as r:
            for raw in r:
                raw = raw.decode("utf-8", "ignore").strip()
                if not raw.startswith("data:") or raw.endswith("[DONE]"):
                    continue
                try:
                    delta = json.loads(raw[5:])["choices"][0]["delta"].get("content") or ""
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    def stream_lines(self, messages, max_tokens=300):
        """Yield complete non-empty lines from a streaming response."""
        buf = ""
        for delta in self.stream_chunks(messages, max_tokens=max_tokens):
            buf += delta
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.strip():
                    yield line
        if buf.strip():
            yield buf
