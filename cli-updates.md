# Kamiwaza CLI Implementation Guide

## Implementation Plan

### Step 1: Refactor Existing Code
1. Move current `run` command logic to new `serve` command
2. Extract download logic from current `run` into `ensure_model_pulled`
3. Extract deployment logic from current `run` into `ensure_model_served`
4. Update imports and dependencies in `setup.py`

### Step 2: Add Core Helper Functions
1. Create `utils.py`:
   - Add progress bar utilities
   - Add error handling decorator
   - Add OpenAI client helpers
2. Implement core functions:
   - `ensure_model_pulled`
   - `ensure_model_served`
   - `interactive_chat`

### Step 3: Implement New Commands
1. Add `pull` command:
   - Use `ensure_model_pulled`
   - Add download-only functionality
2. Add `serve` command:
   - Migrate existing `run` logic
   - Use `ensure_model_pulled`
   - Use `ensure_model_served`
3. Add `list` command:
   - Implement model listing
   - Add file count display
4. Add new `run` command:
   - Implement chat functionality
   - Use both helper functions
   - Add streaming support

### Step 4: Update Command Structure
1. Update `main.py`:
   - Add new command imports
   - Update CLI group
   - Add new command registrations
2. Update command help text
3. Update command documentation

### Step 5: Testing Plan
1. Test individual commands:
   - `pull`: Test download functionality
   - `serve`: Test deployment
   - `list`: Test display
   - `run`: Test chat
2. Test integration:
   - Download → Deploy flow
   - Deploy → Chat flow
   - Full flow with new model

### Step 6: Documentation
1. Update README
2. Add command examples
3. Update help text
4. Add error messages

## Core Functions

```python
def ensure_model_pulled(client, model: str) -> tuple[UUID, str]:
    """
    Ensure model is downloaded, pull if not.
    Returns (model_id, model_name) tuple.
    """
    # Check if model exists in downloaded models
    models = client.models.list_models(load_files=True)
    model_match = next(
        (m for m in models if m.repo_modelId.lower() == model.lower()),
        None
    )
    
    if model_match:
        return model_match.id, model_match.name
        
    # Need to download - initiate download
    console.print(f"🚀 Downloading {model}...")
    download_info = client.models.initiate_model_download(
        repo_id=model,
        quantization='q6_k'
    )
    
    # Monitor download progress
    with create_progress() as progress:
        task = progress.add_task("⏳ Downloading...", total=100)
        
        while True:
            status = client.models.check_download_status(model)
            if status:
                # Calculate average progress across all files
                avg_progress = sum(s.download_percentage for s in status) / len(status)
                progress.update(task, completed=avg_progress)
                
                if all(s.download_percentage == 100 for s in status):
                    break
            time.sleep(1)
    
    console.print("✨ Download complete!")
    return download_info['model'].id, download_info['model'].name

def ensure_model_served(client, model_id: UUID, model_name: str) -> str:
    """
    Ensure model is deployed, deploy if not.
    Returns endpoint URL.
    """
    # Check existing deployments
    deployments = client.serving.list_active_deployments()
    deployment = next(
        (d for d in deployments if str(d.m_id) == str(model_id)),
        None
    )
    
    if deployment and deployment.is_available:
        console.print(f"✨ Using existing deployment of {model_name}")
        return deployment.endpoint
        
    # Need to deploy
    console.print(f"🚀 Deploying {model_name}...")
    
    # Get default config
    configs = client.models.get_model_configs(model_id)
    if not configs:
        raise ValueError("No configurations found for this model")
    default_config = next((config for config in configs if config.default), configs[0])
    
    # Deploy model
    deployment_id = client.serving.deploy_model(
        model_id=model_id,
        m_config_id=default_config.id
    )
    
    # Wait for deployment to be ready
    with create_progress() as progress:
        task = progress.add_task("⏳ Starting deployment...", total=100)
        
        while True:
            deployments = client.serving.list_active_deployments()
            deployment = next(
                (d for d in deployments if str(d.id) == str(deployment_id)),
                None
            )
            
            if deployment and deployment.is_available:
                progress.update(task, completed=100)
                break
                
            time.sleep(1)
    
    console.print("✨ Deployment ready!")
    return deployment.endpoint

def interactive_chat(openai_client):
    """Run interactive chat session."""
    console.print("\n🤖 Chat session started (Ctrl+C to exit)\n")
    
    messages = [
        {"role": "system", "content": "You are a helpful AI assistant."}
    ]
    
    try:
        while True:
            # Get user input
            user_input = input("User: ")
            
            # Exit conditions
            if user_input.lower() in ['exit', 'quit', 'q']:
                break
                
            # Add user message
            messages.append({"role": "user", "content": user_input})
            
            # Get streaming response
            response = openai_client.chat.completions.create(
                model="local-model",
                messages=messages,
                stream=True
            )
            
            # Print assistant prefix
            print("Assistant: ", end="", flush=True)
            
            # Collect assistant message
            assistant_message = ""
            
            # Stream response
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    assistant_message += content
            
            # Add assistant message to history
            messages.append({"role": "assistant", "content": assistant_message})
            print("\n")  # New line after response
            
    except KeyboardInterrupt:
        console.print("\n\n✨ Chat session ended")
```

## Command Implementation

```python
@click.command(name='pull')
@click.argument('model')
@handle_error
def pull_cmd(model: str):
    """Download a model."""
    client = get_client()
    model_id, model_name = ensure_model_pulled(client, model)
    console.print(f"✨ Model {model_name} downloaded successfully!")
    return 0

@click.command(name='serve')
@click.argument('model')
@handle_error
def serve_cmd(model: str):
    """Deploy a model as API."""
    client = get_client()
    
    # Ensure model is pulled
    model_id, model_name = ensure_model_pulled(client, model)
    
    # Deploy model
    endpoint = ensure_model_served(client, model_id, model_name)
    console.print(f"✨ Model deployed at: {endpoint}")
    return 0

@click.command(name='run')
@click.argument('model')
@handle_error
def run_cmd(model: str):
    """Interactive chat with a model."""
    client = get_client()
    
    # Ensure model is pulled
    model_id, model_name = ensure_model_pulled(client, model)
    
    # Ensure model is served
    endpoint = ensure_model_served(client, model_id, model_name)
    
    # Get OpenAI client
    openai_client = client.openai.get_client(model=model_name)
    
    # Start interactive chat
    interactive_chat(openai_client)
    return 0

@click.command(name='list')
@handle_error
def list_cmd():
    """List downloaded models."""
    client = get_client()
    models = client.models.list_models(load_files=True)
    
    if not models:
        console.print("No models downloaded")
        return 0
        
    table = Table(show_header=True, header_style="bold", show_lines=True)
    table.add_column("MODEL", style="cyan")
    table.add_column("REPO ID", style="blue")
    table.add_column("FILES", style="green")
    
    for model in models:
        files = client.models.get_model_files_by_model_id(model.id)
        file_count = len(files) if files else 0
        
        table.add_row(
            model.name,
            model.repo_modelId,
            str(file_count)
        )
    
    console.print(table)
    return 0

# Update main.py to use new commands
def cli():
    """Kamiwaza CLI - Simple model deployment and chat."""
    pass

cli.add_command(run_cmd)    # Interactive chat
cli.add_command(pull_cmd)   # Download only
cli.add_command(serve_cmd)  # Deploy as API
cli.add_command(ps_cmd)     # List running
cli.add_command(stop_cmd)   # Stop deployment
cli.add_command(list_cmd)   # List downloaded
```

## Command Usage

```bash
# Download model
kamiwaza pull qwen2.5-7b-instruct

# List downloaded models
kamiwaza list

# Deploy model as API
kamiwaza serve qwen2.5-7b-instruct

# Interactive chat (handles pull/serve if needed)
kamiwaza run qwen2.5-7b-instruct

# List running deployments
kamiwaza ps

# Stop deployment
kamiwaza stop qwen2.5-7b-instruct
```

## Implementation Notes

1. Core Functions:
   - `ensure_model_pulled`: Handles model download if needed
   - `ensure_model_served`: Handles model deployment if needed
   - `interactive_chat`: Manages chat session with streaming

2. Command Structure:
   - Each command is self-contained
   - Shared logic in core functions
   - Error handling via decorator

3. Interactive Chat Features:
   - Streaming responses
   - Message history
   - Clean exit handling
   - Progress indicators

4. Dependencies Used:
   - click: Command structure
   - rich: Terminal formatting
   - openai: Chat interface
