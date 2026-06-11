# Custom models

aichs ships with Claude and OpenAI models built in. You can add any provider that exposes an **OpenAI-compatible API** by creating `AICHS_HOME/models.json` (default `~/.aichs/models.json`).

Custom providers appear automatically in the provider dropdown — no code changes needed.

---

## File format

```json
{
  "providers": {
    "<provider-id>": {
      "api":      "openai-compatible",
      "baseUrl":  "https://...",
      "apiKey":   "ENV_VAR_NAME | !shell-command | literal-key",
      "models": [
        { "id": "model-id", "name": "Display Name" }
      ]
    }
  }
}
```

| Field | Required | Description |
|---|---|---|
| `api` | yes (new providers) | Always `"openai-compatible"` for now |
| `baseUrl` | yes (new providers) | API base URL |
| `apiKey` | yes | Key resolution — see below |
| `models` | yes (new providers) | List of models to expose |
| `contextWindow` | no | Context size in **tokens** for compaction and the usage ring (defaults: Claude 180k, OpenAI-compatible 100k) |
| `temperature` | no | Top-level request temperature, `0.0` to `2.0` |
| `topK` | no | OpenAI-compatible `extra_body.top_k`, integer `-1` or greater |
| `minP` | no | OpenAI-compatible `extra_body.min_p`, `0.0` to `1.0` |

Generation fields can be set on a provider as defaults or on an individual model as an override. `topK` and `minP` are only sent for OpenAI-compatible requests.

### API key formats

| Value | Resolved as |
|---|---|
| `"GEMINI_API_KEY"` | Read env var `$GEMINI_API_KEY` |
| `"!op read op://vault/gemini/key"` | Run shell command, use stdout |
| `"AIza..."` | Used literally (not recommended — use an env var) |

### Overriding a built-in provider

Omit `api` and `models` to only override `baseUrl` or `apiKey` on `claude` / `openai`:

```json
{
  "providers": {
    "openai": {
      "baseUrl": "https://my-corporate-proxy.example.com/v1"
    }
  }
}
```

---

## Examples

### Google Gemini

Google exposes an OpenAI-compatible endpoint for all Gemini models. See [current model IDs](https://ai.google.dev/gemini-api/docs/models) — Gemini 2.0 is deprecated; prefer 3.x.

Get an API key at [aistudio.google.com](https://aistudio.google.com).

```json
{
  "providers": {
    "google": {
      "api":     "openai-compatible",
      "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "apiKey":  "GEMINI_API_KEY",
      "models": [
        { "id": "gemini-3.5-flash",         "name": "Gemini 3.5 Flash" },
        { "id": "gemini-3.1-pro-preview",   "name": "Gemini 3.1 Pro" },
        { "id": "gemini-3-flash-preview",   "name": "Gemini 3 Flash" },
        { "id": "gemini-3.1-flash-lite",    "name": "Gemini 3.1 Flash Lite" }
      ]
    }
  }
}
```

```bash
export GEMINI_API_KEY=AIza...
```

---

### Ollama (local models)

[Ollama](https://ollama.com) runs models on your machine with no API key or internet connection required.

**1. Install Ollama**

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows — download the installer from https://ollama.com/download
```

**2. Pull models**

```bash
ollama pull llama3.1:8b          # general purpose, fast
ollama pull qwen2.5-coder:7b     # coding-focused, recommended
ollama pull qwen2.5-coder:32b    # better quality, needs ~20 GB RAM
ollama pull deepseek-coder-v2    # strong at code, ~8 GB RAM
```

Browse all available models at [ollama.com/library](https://ollama.com/library).

**3. Start the server** (runs automatically on macOS after install; on Linux run manually)

```bash
ollama serve
```

Ollama listens on `http://localhost:11434` by default.

**4. Add to `AICHS_HOME/models.json`**

```json
{
  "providers": {
    "ollama": {
      "api":     "openai-compatible",
      "baseUrl": "http://localhost:11434/v1",
      "apiKey":  "ollama",
      "contextWindow": 32768,
      "models": [
        { "id": "llama3.1:8b",        "name": "Llama 3.1 8B" },
        { "id": "llama3.1:70b",       "name": "Llama 3.1 70B" },
        { "id": "qwen2.5-coder:7b",   "name": "Qwen 2.5 Coder 7B" },
        { "id": "qwen2.5-coder:32b",  "name": "Qwen 2.5 Coder 32B" },
        { "id": "mistral:7b",         "name": "Mistral 7B" },
        { "id": "deepseek-coder-v2",  "name": "DeepSeek Coder V2" }
      ]
    }
  }
}
```

No API key needed — `"ollama"` is a placeholder the server ignores.

Set `contextWindow` to the size you configured in Ollama (or in **Settings → Models → Edit provider**). Without it, aichs assumes 100k like cloud OpenAI models.

> **Tip:** Only list models you have already pulled. Selecting an unpulled model will return an error from the Ollama server.

**Verify it works**

```bash
curl http://localhost:11434/v1/models
```

Should return a JSON list of your pulled models.

---

### DeepSeek

```json
{
  "providers": {
    "deepseek": {
      "api":     "openai-compatible",
      "baseUrl": "https://api.deepseek.com/v1",
      "apiKey":  "DEEPSEEK_API_KEY",
      "models": [
        { "id": "deepseek-chat",     "name": "DeepSeek V3" },
        { "id": "deepseek-reasoner", "name": "DeepSeek R1" }
      ]
    }
  }
}
```

```bash
export DEEPSEEK_API_KEY=sk-...
```

---

### OpenRouter

[OpenRouter](https://openrouter.ai) proxies 100+ models under a single API key.

```json
{
  "providers": {
    "openrouter": {
      "api":     "openai-compatible",
      "baseUrl": "https://openrouter.ai/api/v1",
      "apiKey":  "OPENROUTER_API_KEY",
      "models": [
        { "id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B" },
        { "id": "mistralai/mistral-large",            "name": "Mistral Large" },
        { "id": "qwen/qwen-2.5-coder-32b-instruct",  "name": "Qwen 2.5 Coder 32B" },
        { "id": "google/gemini-3.1-pro-preview",      "name": "Gemini 3.1 Pro (OR)" }
      ]
    }
  }
}
```

```bash
export OPENROUTER_API_KEY=sk-or-...
```

---

### Proxy / corporate gateway

Route a built-in provider through your own endpoint without changing models:

```json
{
  "providers": {
    "anthropic": {
      "baseUrl": "https://gateway.corp.example.com/anthropic"
    },
    "openai": {
      "baseUrl": "https://gateway.corp.example.com/openai"
    }
  }
}
```

The existing model lists and API keys are preserved; only the base URL changes.

---

### API key from a password manager

Use `!command` to fetch keys at runtime instead of storing them in env vars:

```json
{
  "providers": {
    "google": {
      "api":     "openai-compatible",
      "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "apiKey":  "!op read op://Personal/Gemini/credential",
      "models": [
        { "id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash" }
      ]
    }
  }
}
```

Works with any CLI tool: `op` (1Password), `bw` (Bitwarden), `pass`, `security` (macOS Keychain), etc.

---

## Full example

A `AICHS_HOME/models.json` combining multiple providers:

```json
{
  "providers": {
    "google": {
      "api":     "openai-compatible",
      "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "apiKey":  "GEMINI_API_KEY",
      "models": [
        { "id": "gemini-3.5-flash",       "name": "Gemini 3.5 Flash" },
        { "id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro" }
      ]
    },
    "ollama": {
      "api":     "openai-compatible",
      "baseUrl": "http://localhost:11434/v1",
      "apiKey":  "ollama",
      "models": [
        { "id": "qwen2.5-coder:32b", "name": "Qwen 2.5 Coder 32B" }
      ]
    },
    "deepseek": {
      "api":     "openai-compatible",
      "baseUrl": "https://api.deepseek.com/v1",
      "apiKey":  "DEEPSEEK_API_KEY",
      "models": [
        { "id": "deepseek-chat", "name": "DeepSeek V3" }
      ]
    }
  }
}
```
