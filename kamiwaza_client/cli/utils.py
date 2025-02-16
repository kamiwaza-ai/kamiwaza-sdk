"""CLI utilities for Kamiwaza."""

from functools import wraps
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

console = Console()

def create_progress():
    """Create a consistent progress bar style."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
        transient=True,
        console=console
    )

def format_model_name(name: str) -> str:
    """Convert repo ID to friendly name.
    
    Example: 'Qwen/Qwen2.5-7B-Instruct-GGUF' -> 'qwen2.5-7b-instruct'
    """
    return name.split('/')[-1].lower().replace('-gguf', '')

def handle_error(func):
    """Decorator for consistent error handling."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")
            return 1
    return wrapper 