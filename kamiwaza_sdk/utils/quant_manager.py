import re
from typing import List, Dict, Any, Optional


class QuantizationManager:
    """
    Manages quantization operations throughout the SDK.
    Provides centralized logic for quantization detection, matching, and fallback strategies.
    """
    
    def __init__(self):
        # Define known quantization variants - expanded to include more variants
        self._quant_variants = {
            # Standard quantizations
            'q2_k': ['q2_k', 'q2_k_l', 'q2_k_m', 'q2_k_s'],
            'q3_k': ['q3_k', 'q3_k_l', 'q3_k_m', 'q3_k_s', 'q3_k_xl'],
            'q4_k': ['q4_k', 'q4_k_l', 'q4_k_m', 'q4_k_s'],
            'q4_0': ['q4_0'],
            'q4_1': ['q4_1'],
            'q5_k': ['q5_k', 'q5_k_l', 'q5_k_m', 'q5_k_s'],
            'q6_k': ['q6_k', 'q6_k_l', 'q6_k_m', 'q6_k_s'],
            'q8_0': ['q8_0'],
            
            # Integer quantizations
            'iq1': ['iq1_m', 'iq1_s'],
            'iq2': ['iq2_m', 'iq2_s', 'iq2_xs', 'iq2_xxs'],
            'iq3': ['iq3_m', 'iq3_s', 'iq3_xs'],
            'iq4': ['iq4_nl', 'iq4_xs'],
            
            # Floating point
            'fp16': ['fp16']
        }
        
        # Define priority order for fallback (from highest to lowest quality)
        self._priority_order = [
            'fp16',  # Best quality, most memory
            'q6_k',  # High quality, reasonable memory
            'q5_k',
            'q4_k',
            'q4_0',
            'q4_1', 
            'q3_k',
            'q2_k',  # Lower quality, lowest memory
            'q8_0'   # Anomaly in the pattern - higher number but diff algorithm
        ]
        
        # Compile the regex pattern for detecting quantization formats once
        # Updated pattern to better match all quantization formats, case-insensitive
        self._quant_pattern = re.compile(r'-(q[2-8]_[0-9k]|q[2-8]_k_[lms]|iq[1-4]_[a-z]+|fp\d+)', re.IGNORECASE)

    def detect_quantization(self, filename: str) -> Optional[str]:
        """
        Extract quantization format from a filename.
        
        Args:
            filename (str): The filename to analyze
            
        Returns:
            Optional[str]: The detected quantization or None if not found
        """
        if not filename:
            return None
            
        matches = self._quant_pattern.findall(filename)
        if matches:
            # Get the last match (in case there are multiple)
            quant_match = matches[-1]
            
            # Strip any shard indicators to get clean quantization
            clean_quant = re.sub(r'-\d+(?:-of-\d+)?$', '', quant_match)
            return clean_quant.lower()
        return None

    def match_quantization(self, filename: str, target_quant: str) -> bool:
        """
        Check if a filename matches a target quantization.
        
        Args:
            filename (str): The filename to check
            target_quant (str): The target quantization to match
            
        Returns:
            bool: True if the filename matches the target quantization
        """
        if not filename:
            return False
            
        # Normalize quantization to lowercase for comparison
        target_quant_lower = target_quant.lower()
        
        # Find all quantization patterns in the filename
        matches = self._quant_pattern.findall(filename)
        
        # Check each match against the requested quantization
        for match in matches:
            # Strip any shard indicators to get clean quantization
            clean_match = re.sub(r'-\d+(?:-of-\d+)?$', '', match)
            match_lower = clean_match.lower()
            
            # Check for exact match or as a prefix
            if (match_lower == target_quant_lower or 
                match_lower.startswith(f"{target_quant_lower}_")):
                return True
                
        return False

    def get_fallback_quantizations(self, requested_quant: str) -> List[str]:
        """
        Generate a prioritized list of fallback quantizations.
        
        Args:
            requested_quant (str): The initially requested quantization
            
        Returns:
            List[str]: A prioritized list of alternative quantizations to try
        """
        base_quant = requested_quant.lower().split('_')[0] + '_' + requested_quant.lower().split('_')[1] \
            if '_' in requested_quant else requested_quant.lower()
            
        fallbacks = []
        
        # First try variants of the same base quantization
        if base_quant in self._quant_variants:
            fallbacks.extend(self._quant_variants[base_quant])
            
        # Then try other quantizations in order of quality preference
        current_index = self._priority_order.index(base_quant) if base_quant in self._priority_order else -1
        
        if current_index >= 0:
            # Try better quality options first (items before current_index)
            for i in range(current_index - 1, -1, -1):
                better_quant = self._priority_order[i]
                if better_quant != base_quant:
                    fallbacks.extend(self._quant_variants[better_quant])
                    
            # Then try worse quality options
            for i in range(current_index + 1, len(self._priority_order)):
                worse_quant = self._priority_order[i]
                if worse_quant != base_quant:
                    fallbacks.extend(self._quant_variants[worse_quant])
        
        return fallbacks

    def filter_files_by_quantization(self, files: List[Any], target_quant: str, apply_fallback: bool = True) -> List[Any]:
        """
        Filter a list of files by target quantization, with optional fallback.
        
        Args:
            files (List[Any]): List of file objects that have a 'name' attribute
            target_quant (str): The desired quantization
            apply_fallback (bool): Whether to apply fallback strategy if no exact match is found
            
        Returns:
            List[Any]: Filtered list of files matching the target quantization
        """
        # Try exact match first
        compatible_files = [
            file for file in files 
            if self.match_quantization(file.name, target_quant)
        ]
        
        # If no match and fallback is enabled
        if not compatible_files and apply_fallback:
            # If variant wasn't specified, try all variants of the base quantization
            if '_' not in target_quant or target_quant.count('_') == 1:
                fallbacks = self.get_fallback_quantizations(target_quant)
                
                for fallback_quant in fallbacks:
                    compatible_files = [
                        file for file in files 
                        if self.match_quantization(file.name, fallback_quant)
                    ]
                    
                    if compatible_files:
                        break
                        
        return compatible_files
        
    def has_multiple_quantizations(self, files: List[Any]) -> bool:
        """
        Determine if the files list contains multiple distinct quantizations.
        
        Args:
            files (List[Any]): List of file objects with 'name' attribute
            
        Returns:
            bool: True if multiple quantizations detected, False otherwise
        """
        # Extract quantizations from filenames
        quantizations = set()
        for file in files:
            if hasattr(file, 'name') and file.name:
                quant = self.detect_quantization(file.name)
                if quant:
                    quantizations.add(quant)
        
        # Return True if more than one quantization found
        return len(quantizations) > 1
