# Kamiwaza CLI Documentation

Kamiwaza CLI provides a simple interface for downloading, deploying, and interacting with AI models.

## Quick Start

```bash
# First time setup - configure your server
kamiwaza config set-url http://your-server:7777/api

# Download and chat with a model in one command
kamiwaza run qwen2.5-7b-instruct
```

## Commands

### `run <model>`
Start an interactive chat session with a model. If the model isn't downloaded or deployed, it will handle that automatically.

```bash
$ kamiwaza run qwen2.5-7b-instruct
🚀 Deploying Qwen2.5-7B-Instruct-GGUF...
✨ Deployment ready!

🤖 Chat session started (Ctrl+C to exit)

User: hey!!
Assistant: Hello there! How can I assist you today?

User: ^C

✨ Chat session ended
```

### `pull <model>`
Download a model without deploying it.

```bash
$ kamiwaza pull qwen2.5-7b-instruct
🚀 Downloading qwen2.5-7b-instruct...
⏳ Downloading... [====================] 100%
✨ Download complete!
```

### `serve <model>`
Deploy a model as an API endpoint. Downloads the model if not already present.

```bash
$ kamiwaza serve qwen2.5-7b-instruct
🚀 Deploying Qwen2.5-7B-Instruct-GGUF...
✨ Deployment ready!
✨ Model deployed at: http://localhost:51114/v1
```

### `ps`
List all running model deployments.

```bash
$ kamiwaza ps
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ MODEL                     ┃ STATUS       ┃ ENDPOINT                  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Qwen2.5-7B-Instruct-GGUF  │ ● RUNNING    │ http://localhost:51113/v1 │
│ Qwen2.5-32B-Instruct-GGUF │ ● RUNNING    │ http://localhost:51100/v1 │
└───────────────────────────┴──────────────┴───────────────────────────┘
```

### `list`
Show all downloaded models and their file counts.

```bash
$ kamiwaza list
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ MODEL                     ┃ REPO ID                        ┃ FILES ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Qwen2.5-7B-Instruct-GGUF  │ Qwen/Qwen2.5-7B-Instruct-GGUF  │ 22    │
│ Qwen2.5-32B-Instruct-GGUF │ Qwen/Qwen2.5-32B-Instruct-GGUF │ 67    │
└───────────────────────────┴────────────────────────────────┴───────┘
```

### `stop <model>`
Stop a running model deployment.

```bash
$ kamiwaza stop qwen2.5-7b-instruct
🛑 Stopping Qwen2.5-7B-Instruct-GGUF...
✨ Model stopped
```

### `config`
Manage CLI configuration.

#### `config set-url <url>`
Set the Kamiwaza API URL.
```bash
$ kamiwaza config set-url http://localhost:7777/api
✨ API URL set to: http://localhost:7777/api
```

#### `config show`
Display current configuration.
```bash
$ kamiwaza config show
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ KEY     ┃ VALUE                      ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ base_url│ http://localhost:7777/api  │
└─────────┴──────────────────────────┘
```

## Supported Models

The CLI currently supports friendly names for common models (more coming soon):

- `qwen2.5-7b-instruct` → `Qwen/Qwen2.5-7B-Instruct-GGUF`

You can use either the friendly name or the full repository ID in any command.

## Tips

1. Use `Ctrl+C` to gracefully exit chat sessions
2. Models are downloaded with optimized quantization (q6_k by default)
3. The CLI will reuse existing deployments when possible
4. Use the friendly model names for easier typing
