class ProgressFormatter:
    """
    Utility class for formatting progress information.
    Provides methods for formatting sizes, speeds, and time durations.
    """
    
    @staticmethod
    def format_elapsed_time(seconds: float) -> str:
        """
        Format elapsed time in MM:SS format to match API output.
        
        Args:
            seconds (float): The elapsed time in seconds.
            
        Returns:
            str: The formatted time string.
        """
        # Convert seconds to integer to avoid formatting issues
        seconds = int(seconds)
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"
    
    @staticmethod
    def format_size(size_in_bytes: int) -> str:
        """
        Format size in human-readable format.
        
        Args:
            size_in_bytes (int): The size in bytes.
            
        Returns:
            str: The formatted size string.
        """
        if not size_in_bytes:
            return "unknown size"
        if size_in_bytes < 1024:
            return f"{size_in_bytes} B"
        elif size_in_bytes < 1024 * 1024:
            return f"{size_in_bytes/1024:.2f} KB"
        elif size_in_bytes < 1024 * 1024 * 1024:
            return f"{size_in_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_in_bytes/(1024*1024*1024):.2f} GB"
    
    @staticmethod
    def format_speed(speed_in_bytes: float) -> str:
        """
        Format download speed in human-readable format.
        
        Args:
            speed_in_bytes (float): The speed in bytes per second.
            
        Returns:
            str: The formatted speed string.
        """
        if speed_in_bytes < 1024:
            return f"{speed_in_bytes:.2f} B/s"
        elif speed_in_bytes < 1024 * 1024:
            return f"{speed_in_bytes/1024:.2f} KB/s"
        else:
            return f"{speed_in_bytes/(1024*1024):.2f} MB/s"
    
    @staticmethod
    def format_time(seconds: float) -> str:
        """
        Format time in human-readable format.
        
        Args:
            seconds (float): The time in seconds.
            
        Returns:
            str: The formatted time string.
        """
        if seconds < 60:
            return f"{seconds:.0f} seconds"
        elif seconds < 3600:
            minutes = seconds // 60
            sec = seconds % 60
            return f"{minutes:.0f}:{sec:02d} minutes"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours:.0f}h {minutes:.0f}m"
    
    @staticmethod
    def format_progress_bar(percentage: float, width: int = 20) -> str:
        """
        Format a text-based progress bar.
        
        Args:
            percentage (float): The progress percentage (0-100).
            width (int, optional): The width of the progress bar. Defaults to 20.
            
        Returns:
            str: The formatted progress bar string.
        """
        filled_width = int(width * percentage / 100)
        bar = '█' * filled_width + '░' * (width - filled_width)
        return f"[{bar}] {percentage:.1f}%"
    
    @staticmethod
    def format_countdown(seconds: int) -> str:
        """
        Format a countdown timer.
        
        Args:
            seconds (int): The time in seconds.
            
        Returns:
            str: The formatted countdown string.
        """
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            sec = seconds % 60
            return f"{minutes}m {sec}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            sec = seconds % 60
            return f"{hours}h {minutes}m {sec}s"
