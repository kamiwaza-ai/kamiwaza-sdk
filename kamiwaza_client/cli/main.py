# kamiwaza/kamiwaza-sdk/kamiwaza_client/cli/main.py
"""Kamiwaza CLI main entry point."""

import click
from .commands import (
    run_cmd, ps_cmd, stop_cmd, config_cmd,
    pull_cmd, serve_cmd, list_cmd
)
from .utils import console

@click.group()
def cli():
    """Kamiwaza CLI - Simple model deployment and chat.
    
    Run AI models with a single command:
        $ kamiwaza run qwen2.5-7b-instruct
        
    First time setup:
        $ kamiwaza config set-url http://your-server:7777/api
        
    Basic usage:
        $ kamiwaza pull qwen2.5-7b-instruct  # Download model
        $ kamiwaza serve qwen2.5-7b-instruct  # Deploy as API
        $ kamiwaza run qwen2.5-7b-instruct    # Interactive chat
    """
    pass

# Add commands
cli.add_command(run_cmd)     # Interactive chat
cli.add_command(pull_cmd)    # Download only
cli.add_command(serve_cmd)   # Deploy as API
cli.add_command(ps_cmd)      # List running
cli.add_command(stop_cmd)    # Stop deployment
cli.add_command(list_cmd)    # List downloaded
cli.add_command(config_cmd)  # Manage config

if __name__ == '__main__':
    cli() 