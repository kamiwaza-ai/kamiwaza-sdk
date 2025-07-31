# Changelog

All notable changes to the Kamiwaza SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2024-01-31

### Added
- **App Garden Service** (`client.apps`) - Deploy and manage containerized applications
  - Deploy applications from pre-built templates
  - Manage application lifecycle (start, stop, scale)
  - Environment variable configuration
  - Instance monitoring and management
  - No authentication required for easy access

- **Tool Shed Service** (`client.tools`) - Deploy and manage MCP Tool servers
  - Deploy MCP-compatible Tool servers for AI assistants
  - Pre-built templates for common integrations (web search, databases, etc.)
  - Tool capability discovery
  - Health monitoring
  - Requires authentication for security

- **Authentication Improvements**
  - Added `UserPasswordAuthenticator` for username/password authentication
  - Fixed authentication flow to support both Authorization headers and cookies
  - Improved token refresh mechanism
  - Better error handling for authentication failures

- **New Example Notebook**
  - `08_app_garden_and_tools.ipynb` - Comprehensive guide to using App Garden and Tool Shed

- **Documentation**
  - Added complete documentation for App Garden service
  - Added complete documentation for Tool Shed service
  - Updated README with all example notebooks

### Fixed
- Authentication token not being sent in requests
- Circular authentication issue when refreshing tokens
- Cookie-based authentication for services that require it

### Changed
- Version bumped from 0.3.3.0 to 0.5.0
- SDK now defers authentication until first request instead of during initialization

## [0.3.3.0] - Previous Release

### Added
- Initial SDK implementation with core services
- Model management and deployment
- Vector database integration
- OpenAI-compatible API
- Authentication framework
- Example notebooks for common use cases