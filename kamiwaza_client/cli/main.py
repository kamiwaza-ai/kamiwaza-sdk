"""Kamiwaza CLI main entry point."""

import click
from .commands import run_cmd, ps_cmd, stop_cmd, config_cmd
from .utils import console

@click.group()
def cli():
    """Kamiwaza CLI - Simple model deployment.
    
    Run AI models with a single command:
        $ kamiwaza run qwen2.5-7b-instruct
        
    First time setup:
        $ kamiwaza config set-url http://your-server:7777/api
    """
    pass

# Add commands
cli.add_command(run_cmd)
cli.add_command(ps_cmd)
cli.add_command(stop_cmd)
cli.add_command(config_cmd)

if __name__ == '__main__':
    cli() 