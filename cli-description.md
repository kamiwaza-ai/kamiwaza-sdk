# Kamiwaza CLI PRD

## Background & Motivation

Kamiwaza is an enterprise-grade AI infrastructure platform with a robust Python SDK that enables model deployment and management. While powerful, the current SDK requires writing Python code and understanding multiple steps to deploy models. To improve developer experience and increase adoption, we need a simple command-line interface similar to Ollama.

The goal is to let developers deploy models with single commands, while leveraging Kamiwaza's powerful infrastructure under the hood.

## The Problem

Currently, deploying a model with Kamiwaza requires multiple steps in Python:

```python
# Current process requires multiple steps and Python code
client = KamiwazaClient()
download_info = client.models.initiate_model_download("Qwen/Qwen2.5-7B-Instruct-GGUF", quantization='q6_k')
# Wait for download...
model_id = download_info['model'].id
deployment_id = client.serving.deploy_model(model_id)
```

This process is:
- Too complex for simple use cases
- Requires understanding the SDK
- Involves multiple steps
- Needs Python coding

## The Solution

A simple CLI that turns the above into:

```bash
$ kamiwaza run qwen2.5-7b-instruct
🚀 Downloading Qwen 2.5 7B Instruct...
✨ Model deployed! 

# Check status
$ kamiwaza ps
MODEL                  STATUS    
qwen2.5-7b-instruct   running   

# Stop model
$ kamiwaza stop qwen2.5-7b-instruct
✨ Model stopped
```

## Core Requirements

### 1. Simple Commands
- `run`: Download and deploy a model
- `ps`: List running models
- `stop`: Stop a model

### 2. Initial Model Support
For the first version, we will support one model:
- Command name: `qwen2.5-7b-instruct`
- Actual repo: `Qwen/Qwen2.5-7B-Instruct-GGUF`
- Default quantization: `q6_k`

### 3. User Experience
- Clear progress indicators during download/deployment
- Simple error messages in plain language
- No configuration needed for basic usage

## Desired User Flow

1. First-time usage:
```bash
$ pip install kamiwaza-cli

$ kamiwaza run qwen2.5-7b-instruct
🚀 Downloading Qwen 2.5 7B Instruct...
[████████████] 100% | ETA: 0s
✨ Model deployed! 
```

2. Checking status:
```bash
$ kamiwaza ps
MODEL                  STATUS    
qwen2.5-7b-instruct   running   
```

3. Stopping model:
```bash
$ kamiwaza stop qwen2.5-7b-instruct
✨ Model stopped
```

## Technical Integration

The CLI should be integrated into the existing Kamiwaza SDK, leveraging the current ModelService and ServingService functionality. This ensures we maintain all the robust capabilities of Kamiwaza while providing a simpler interface.

The CLI should:
1. Map friendly model names to actual repo IDs
2. Use the SDK's existing download and deployment capabilities
3. Handle errors gracefully with user-friendly messages

## Success Criteria

1. Users can deploy models with a single command
2. No configuration required for basic usage
3. Clear progress indication during operations
4. Simple status checking

## Non-Goals for V1

1. Multiple model support (beyond qwen2.5-7b-instruct)
2. Advanced configuration options
3. Custom deployment options
4. Resource management features
5. Model fine-tuning capabilities

## Future Considerations

While out of scope for V1, these should be kept in mind for the design:
1. Adding support for more models
2. Configuration options for advanced users
3. Resource usage monitoring
4. Deployment templates
5. Custom model support

## Documentation Needs

1. Installation instructions
2. Basic usage guide (`run`, `ps`, `stop`)
3. Common error solutions

The focus for V1 is on creating a dead-simple interface for deploying the Qwen model, similar to how Ollama made local model deployment accessible to everyone. All the power of Kamiwaza should still be available through the SDK for advanced users, but the CLI should make basic usage trivial.