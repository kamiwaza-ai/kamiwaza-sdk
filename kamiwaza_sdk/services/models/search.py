from typing import List, Optional, Dict, Any, Union, Set
from uuid import UUID
from ...schemas.models.model import Model
from ...schemas.models.model_search import ModelSearchRequest, ModelSearchResponse, HubModelFileSearch


class ModelSearchMixin:
    """Mixin for model search functionality."""
    
    def search_models(self, query: str, exact: bool = False, limit: int = 100, 
                     hubs_to_search: Optional[List[str]] = None, 
                     load_files: bool = True) -> List[Model]:
        """
        Search for models based on a query string.

        Args:
            query (str): The search query.
            exact (bool, optional): Whether to perform an exact match. Defaults to False.
            limit (int, optional): Maximum number of results to return. Defaults to 100.
            hubs_to_search (List[str], optional): List of hubs to search in. Defaults to None (search all hubs).
            load_files (bool, optional): Whether to load file information for each model. Defaults to True.

        Returns:
            List[Model]: A list of matching models.
        """
        search_request = ModelSearchRequest(
            query=query,
            exact=exact,
            limit=limit,
            hubs_to_search=hubs_to_search or ["*"]
        )
        response = self.client._request("POST", "/models/search/", json=search_request.model_dump())
        search_response = ModelSearchResponse.model_validate(response)
        result_models = [result.model for result in search_response.results]
        
        # Load file information for each model if requested
        if load_files and result_models:
            for model in result_models:
                try:
                    # Search for files for this model
                    if model.repo_modelId and model.hub:
                        files = self.search_hub_model_files(
                            HubModelFileSearch(hub=model.hub, model=model.repo_modelId)
                        )
                        # Add files to the model
                        model.m_files = files
                        
                        # Extract quantization information using the QuantizationManager
                        quants = set()
                        for file in files:
                            if file.name:
                                quant = self.quant_manager.detect_quantization(file.name)
                                if quant:
                                    quants.add(quant)
                        
                        # Store available quantizations in the model for display
                        model.available_quantizations = sorted(list(quants))
                except Exception as e:
                    print(f"Error loading files for model {model.repo_modelId}: {e}")
        
        # Add a summary line at the beginning when printing
        if result_models:
            original_models = result_models.copy()
            class EnhancedModelList(list):
                def __str__(self):
                    count = len(self)
                    if count == 0:
                        return "No models found matching your query."
                    else:
                        summary = f"Found {count} model{'s' if count > 1 else ''} matching '{query}':\n"
                        model_strings = [str(model) for model in self]
                        return summary + "\n\n".join(model_strings)
                        
            enhanced_models = EnhancedModelList(original_models)
            return enhanced_models
        
        return result_models
    
    def _get_exact_quant_match(self, filename: str, quantization: str) -> bool:
        """
        Check if a filename matches exactly a quantization pattern.
        
        Args:
            filename (str): The filename to check
            quantization (str): The quantization pattern to match
            
        Returns:
            bool: True if exact match found, False otherwise
        """
        # Use the QuantizationManager for matching
        return self.quant_manager.match_quantization(filename, quantization)
