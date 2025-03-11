# ModelService Refactoring Checklist

This checklist outlines the step-by-step process for implementing the refactoring plan described in `MODELS_REFACTOR.MD`.

## Phase 1: Setup Directory Structure

- [x] Create the `services/models` directory
- [x] Create empty files for each module:
  - [x] `__init__.py`
  - [x] `base.py`
  - [x] `search.py`
  - [x] `downloads.py`
  - [x] `files.py`
  - [x] `configs.py`
  - [x] `compatibility.py`
- [x] Create empty files for new utility classes:
  - [x] `utils/download_tracker.py`
  - [x] `utils/progress_formatter.py`

## Phase 2: Implement Mixins

- [x] Implement `ModelSearchMixin` in `search.py`
  - [x] Move `search_models` method
  - [x] Move `_get_exact_quant_match` method
  - [x] Add necessary imports

- [x] Implement `ModelDownloadMixin` in `downloads.py`
  - [x] Move `initiate_model_download` method
  - [x] Move `check_download_status` method
  - [x] Move `get_model_files_download_status` method
  - [x] Move `wait_for_download` method
  - [x] Move `_display_progress` method
  - [x] Move `download_and_deploy_model` method
  - [x] Move `get_model_download_status` method
  - [x] Add necessary imports
  - [x] Add note about investigating `check_download_status` vs `get_model_files_download_status`

- [x] Implement `ModelFileMixin` in `files.py`
  - [x] Move `get_model_memory_usage` method
  - [x] Move `delete_model_file` method
  - [x] Move `get_model_file` method
  - [x] Move `get_model_files_by_model_id` method
  - [x] Move `list_model_files` method
  - [x] Move `create_model_file` method
  - [x] Move `search_hub_model_files` method
  - [x] Move `get_model_file_memory_usage` method
  - [x] Add necessary imports

- [x] Implement `ModelConfigMixin` in `configs.py`
  - [x] Move `create_model_config` method
  - [x] Move `get_model_configs` method
  - [x] Move `get_model_configs_for_model` method
  - [x] Move `get_model_config` method
  - [x] Move `delete_model_config` method
  - [x] Move `update_model_config` method
  - [x] Add necessary imports

- [x] Implement `CompatibilityMixin` in `compatibility.py`
  - [x] Move `_get_server_os` method
  - [x] Move `filter_compatible_models` method
  - [x] Move `_filter_files_for_os` method
  - [x] Add necessary imports

## Phase 3: Implement Utility Classes

- [x] Implement `DownloadTracker` in `utils/download_tracker.py`
  - [x] Move `_calculate_overall_progress` method
  - [x] Add necessary imports

- [x] Implement `ProgressFormatter` in `utils/progress_formatter.py`
  - [x] Move `_format_elapsed_time` method
  - [x] Move `_format_size` method
  - [x] Move `_format_speed` method
  - [x] Move `_format_time` method
  - [x] Add necessary imports

## Phase 4: Implement Core ModelService

- [x] Implement `ModelService` in `base.py`
  - [x] Move `__init__` method
  - [x] Move `get_model` method
  - [x] Move `create_model` method
  - [x] Move `delete_model` method
  - [x] Move `list_models` method
  - [x] Move `get_model_by_repo_id` method
  - [x] Add necessary imports
  - [x] Inherit from all mixins

- [x] Update `__init__.py` to export `ModelService`

## Phase 5: Testing and Cleanup

- [ ] Run tests to ensure functionality is maintained
- [ ] Fix any issues that arise
- [ ] Remove the original `models.py` file once everything is working
- [ ] Update imports in any files that directly imported from `models.py`

## Phase 6: Documentation

- [ ] Update documentation to reflect the new structure
- [ ] Add docstrings to new classes and methods
- [ ] Add comments explaining complex logic

## Progress Tracking

- [x] Phase 1 completed
- [x] Phase 2 completed
- [x] Phase 3 completed
- [x] Phase 4 completed (STOP ONCE WE GET HERE AND ONLY ALLOS HUMANS TO DO STEPs 5-6)
- [ ] Phase 5 completed 
- [ ] Phase 6 completed
- [ ] Refactoring complete!
