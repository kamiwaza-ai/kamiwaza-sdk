# Kamiwaza App Garden API Endpoints

This document provides a comprehensive overview of all App Garden-related API endpoints in the Kamiwaza platform.

## Base URL Structure

The Kamiwaza API is organized into several routers:
- **App Router**: `/apps` - For app deployment management
- **Template Router**: `/apps` - For app template management
- **Tool Router**: `/tool` - For Tool Shed (MCP tool server) management

## App Deployment Endpoints

### 1. Deploy App
- **Endpoint**: `POST /apps/deploy_app`
- **Description**: Deploy a new application
- **Request Body**: `CreateAppDeployment`
  ```json
  {
    "name": "string",
    "template_id": "uuid",
    "min_copies": 1,
    "starting_copies": 1,
    "max_copies": null,
    "serve_path": "string",
    "lb_port": 0,
    "port_mappings": [...],
    "runtime_resources_id": "uuid",
    "env_vars": {"key": "value"},
    "preferred_model_type": "any|large|reasoning|fast|vl",
    "fail_if_model_type_unavailable": false,
    "preferred_model_name": "string",
    "fail_if_model_name_unavailable": false
  }
  ```
- **Response**: `AppDeployment`

### 2. List Deployments
- **Endpoint**: `GET /apps/deployments`
- **Description**: List all app deployments (excludes Tool deployments)
- **Response**: `List[AppDeployment]`

### 3. Get Deployment
- **Endpoint**: `GET /apps/deployment/{deployment_id}`
- **Description**: Get details of a specific app deployment
- **Parameters**: 
  - `deployment_id` (UUID): The ID of the deployment
- **Response**: `AppDeployment`

### 4. Stop Deployment
- **Endpoint**: `DELETE /apps/deployment/{deployment_id}`
- **Description**: Stop an app deployment
- **Parameters**: 
  - `deployment_id` (UUID): The ID of the deployment to stop
- **Response**: `boolean`

### 5. List Instances
- **Endpoint**: `GET /apps/instances`
- **Description**: List all app instances, optionally filtered by deployment
- **Query Parameters**:
  - `deployment_id` (UUID, optional): Filter by deployment ID
- **Response**: `List[AppInstance]`

### 6. Get Instance
- **Endpoint**: `GET /apps/instance/{instance_id}`
- **Description**: Get details of a specific app instance
- **Parameters**:
  - `instance_id` (UUID): The ID of the instance
- **Response**: `AppInstance`

### 7. Get Deployment Status
- **Endpoint**: `GET /apps/deployment/{deployment_id}/status`
- **Description**: Get the status of a specific app deployment
- **Parameters**:
  - `deployment_id` (UUID): The ID of the deployment
- **Response**: `string` (status)

## App Template Endpoints

### 1. Create App Template
- **Endpoint**: `POST /apps/app_templates`
- **Description**: Create a new app template
- **Authentication**: Required (user ID injected)
- **Request Body**: `CreateAppTemplate`
  ```json
  {
    "name": "string",
    "version": "1.0.0",
    "source_type": "kamiwaza|user_repo|public",
    "visibility": "private|team|public",
    "compose_yml": "string",
    "risk_tier": 0|1|2,
    "validate_containers": false,
    "env_defaults": {"key": "value"},
    "preferred_model_type": "any|large|reasoning|fast|vl",
    "fail_if_model_type_unavailable": false,
    "preferred_model_name": "string",
    "fail_if_model_name_unavailable": false
  }
  ```
- **Response**: `AppTemplate`

### 2. List App Templates
- **Endpoint**: `GET /apps/app_templates`
- **Description**: List all app templates (excludes Tool templates)
- **Response**: `List[AppTemplate]`

### 3. Get App Template
- **Endpoint**: `GET /apps/app_templates/{template_id}`
- **Description**: Get a specific app template
- **Parameters**:
  - `template_id` (UUID): The ID of the template
- **Response**: `AppTemplate`

### 4. Delete App Template
- **Endpoint**: `DELETE /apps/app_templates/{template_id}`
- **Description**: Delete an app template
- **Parameters**:
  - `template_id` (UUID): The ID of the template
- **Response**: `{"result": "deleted"}`

### 5. List Kamiwaza Garden
- **Endpoint**: `GET /apps/kamiwaza_garden`
- **Description**: List pre-built Kamiwaza app garden templates
- **Response**: `List[dict]`

### 6. Get Garden Status
- **Endpoint**: `GET /apps/garden/status`
- **Description**: Get status of garden apps - available vs imported
- **Response**:
  ```json
  {
    "garden_apps_available": true,
    "total_garden_apps": 10,
    "imported_apps": 7,
    "missing_apps": ["app1", "app2", "app3"],
    "missing_count": 3
  }
  ```

### 7. Import Garden Apps
- **Endpoint**: `POST /apps/garden/import`
- **Description**: Import missing garden apps as templates
- **Authentication**: Required
- **Response**:
  ```json
  {
    "imported_count": 3,
    "total_apps": 10,
    "errors": [],
    "success": true
  }
  ```

### 8. Get Image Status
- **Endpoint**: `GET /apps/images/status/{template_id}`
- **Description**: Check if images for a template have been pulled
- **Parameters**:
  - `template_id` (UUID): The ID of the template
- **Response**:
  ```json
  {
    "template_id": "uuid",
    "images": ["image1:tag", "image2:tag"],
    "image_status": {"image1:tag": true, "image2:tag": false},
    "all_images_pulled": false
  }
  ```

### 9. Pull Template Images
- **Endpoint**: `POST /apps/images/pull/{template_id}`
- **Description**: Pull all images for a template
- **Parameters**:
  - `template_id` (UUID): The ID of the template
- **Response**:
  ```json
  {
    "template_id": "uuid",
    "total_images": 2,
    "successful_pulls": 2,
    "results": [...],
    "all_successful": true
  }
  ```

## Tool Shed Endpoints

### 1. Deploy Tool Server
- **Endpoint**: `POST /tool/deploy`
- **Description**: Deploy a new Tool server from a Docker image
- **Authentication**: Required
- **Request Body**: `CreateToolDeployment`
  ```json
  {
    "name": "my-math-tools",
    "image": "myusername/tool-math-server:latest",
    "env_vars": {
      "API_KEY": "optional-config",
      "LOG_LEVEL": "info"
    },
    "min_copies": 1,
    "max_copies": 1
  }
  ```
- **Response**: `ToolDeployment` (includes generated public URL)

### 2. Deploy from Template
- **Endpoint**: `POST /tool/deploy-template/{template_name}`
- **Description**: Deploy a Tool server from a pre-built template
- **Authentication**: Required
- **Parameters**:
  - `template_name` (string): Name of the template (e.g., "tool-websearch")
- **Request Body**: `DeployFromTemplateRequest`
  ```json
  {
    "name": "my-search-tool",
    "env_vars": {
      "TAVILY_API_KEY": "your-api-key"
    }
  }
  ```
- **Response**: `ToolDeployment`

### 3. List Tool Deployments
- **Endpoint**: `GET /tool/deployments`
- **Description**: List all Tool deployments
- **Authentication**: Required
- **Response**: `List[ToolDeployment]`

### 4. Get Tool Deployment
- **Endpoint**: `GET /tool/deployment/{deployment_id}`
- **Description**: Get details of a specific Tool deployment
- **Authentication**: Required
- **Parameters**:
  - `deployment_id` (UUID): The ID of the deployment
- **Response**: `ToolDeployment`

### 5. Stop Tool Deployment
- **Endpoint**: `DELETE /tool/deployment/{deployment_id}`
- **Description**: Stop and remove a tool deployment
- **Authentication**: Required
- **Parameters**:
  - `deployment_id` (UUID): The ID of the deployment
- **Response**: `{"message": "Tool deployment {id} stopped successfully"}`

### 6. Discover Tool Servers
- **Endpoint**: `GET /tool/discover`
- **Description**: Discover available tool servers and their capabilities
- **Authentication**: Required
- **Response**: `ToolDiscovery`
  ```json
  {
    "servers": [
      {
        "deployment_id": "uuid",
        "name": "math-tools",
        "url": "http://host.docker.internal:51122/mcp/",
        "status": "running",
        "capabilities": [...],
        "created_at": "2024-01-01T00:00:00Z"
      }
    ],
    "total": 1
  }
  ```

### 7. Check Tool Health
- **Endpoint**: `GET /tool/deployment/{deployment_id}/health`
- **Description**: Check the health status of a tool deployment
- **Authentication**: Required
- **Parameters**:
  - `deployment_id` (UUID): The ID of the deployment
- **Response**: `ToolHealthCheck`
  ```json
  {
    "status": "healthy",
    "protocol_version": "1.0",
    "last_checked": "2024-01-01T00:00:00Z",
    "error": null
  }
  ```

### 8. List Available Templates
- **Endpoint**: `GET /tool/templates/available`
- **Description**: List available pre-built Tool templates
- **Authentication**: Required
- **Response**: `List[dict]`

### 9. Get Tool Garden Status
- **Endpoint**: `GET /tool/garden/status`
- **Description**: Get status of Tool garden servers - available vs imported
- **Response**:
  ```json
  {
    "tool_servers_available": true,
    "total_tool_servers": 5,
    "imported_tool_servers": 3,
    "missing_tool_servers": ["tool-server1", "tool-server2"]
  }
  ```

### 10. Import Tool Garden Servers
- **Endpoint**: `POST /tool/garden/import`
- **Description**: Import missing Tool servers from garden as templates
- **Authentication**: Required
- **Response**:
  ```json
  {
    "imported_count": 2,
    "total_servers": 5,
    "errors": [],
    "success": true
  }
  ```

## Data Models

### AppDeployment
- `id`: UUID
- `name`: string
- `template_id`: UUID (optional)
- `requested_at`: datetime
- `deployed_at`: datetime (optional)
- `status`: string
- `instances`: List[AppInstance]
- `created_at`: datetime
- `compose_yml`: string (optional)
- `env_vars`: Dict[str, str] (optional)
- Model preference fields

### AppInstance
- `id`: UUID
- `deployment_id`: UUID
- `deployed_at`: datetime
- `container_id`: string (optional)
- `node_id`: UUID (optional)
- `host_name`: string (optional)
- `listen_port`: int (optional)
- `status`: string
- `port_mappings`: List[AppPortMapping]

### AppTemplate
- `id`: UUID
- `name`: string
- `version`: string
- `source_type`: TemplateSource
- `visibility`: TemplateVisibility
- `compose_yml`: string
- `risk_tier`: RiskTier
- `verified`: boolean
- `created_at`: datetime
- `updated_at`: datetime (optional)
- `description`: string (optional)
- `env_defaults`: Dict[str, str] (optional)
- Model preference fields

### ToolDeployment
- All fields from AppDeployment plus:
- `url`: string (generated public HTTPS URL)
- `deployment_type`: string (always "tool")

## Enums

### TemplateSource
- `kamiwaza`: Official Kamiwaza templates
- `user_repo`: User repository templates
- `public`: Public templates

### TemplateVisibility
- `private`: Only visible to owner
- `team`: Visible to team members
- `public`: Visible to all users

### RiskTier
- `0` (guided): Lowest risk, guided deployment
- `1` (scanned): Medium risk, scanned for security
- `2` (break_glass): High risk, requires explicit approval

## Notes

1. Tool deployments are special app deployments that expose MCP (Model Context Protocol) servers
2. Tool deployments always use `host.docker.internal` for Docker container connectivity
3. Garden apps/tools can be imported from pre-configured JSON files
4. Image pulling can be done separately from deployment for better control
5. Model preferences allow apps to request specific model types or names