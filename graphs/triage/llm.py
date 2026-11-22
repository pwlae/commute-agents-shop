"""The one LLM call of the graph.

Anthropic Messages API by default (ANTHROPIC_API_KEY,
LLM_MODEL, default claude-sonnet-5-5). With LLM_BASE_URL set,
any OpenAI-compatible /chat/completions server instead (a
local model), with LLM_API_KEY if it needs one.
"""
import json, os, urllib.request

def _post(url, body, headers):
    req = urllib.request.Request(
        url, json.dumps(body).encode(),
        {"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

def llm(run, key, system, prompt, max_tokens=800):
    def call():
        run.spend_llm_call()
        base = os.environ.get("LLM_BASE_URL")
        model = os.environ.get("LLM_MODEL")
        if base:
            out = _post(base.rstrip("/") + "/chat/completions",
                {"model": model, "max_tokens": max_tokens,
                 "messages": [
                   {"role": "system", "content": system},
                   {"role": "user", "content": prompt}]},
                {"authorization": "Bearer "
                 + os.environ.get("LLM_API_KEY", "none")})
            return out["choices"][0]["message"]["content"]
        out = _post("https://api.anthropic.com/v1/messages",
            {"model": model or "claude-sonnet-5-5",
             "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
             "anthropic-version": "2023-06-01"})
        return "".join(b["text"] for b in out["content"]
                       if b["type"] == "text")
    return run.outside(key, call)
