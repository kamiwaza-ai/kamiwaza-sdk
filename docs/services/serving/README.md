# Serving Service

## Overview
The Serving Service (`ServingService`) provides comprehensive model deployment and serving capabilities for the Kamiwaza AI Platform. Located in `kamiwaza_sdk/services/serving.py`, this service manages Ray cluster operations, model deployment, and inference requests.

## Key Features
- Ray Service Management
- Model Deployment
- Model Instance Management
- Model Loading/Unloading
- Health Monitoring
- VRAM Estimation

## Ray Service Management

### Available Methods
- `start_ray() -> Dict[str, Any]`: Initialize Ray service
- `get_status() -> Dict[str, Any]`: Get Ray cluster status

```python
# Start Ray service
status = client.serving.start_ray()

# Check Ray status
ray_status = client.serving.get_status()
```

## Model Deployment

### Available Methods
- `estimate_model_vram(model_id: UUID) -> int`: Estimate model VRAM requirements
- `deploy_model(model_id=..., repo_id=..., wait=True, timeout_seconds=3600, **kwargs) -> Union[UUID, bool]`: Deploy a model. The server accepts the request asynchronously and returns the deployment id immediately; with `wait=True` (default) the SDK polls client-side until the deployment is ready, with `wait=False` it returns the id right away
- `wait_deployment_ready(deployment_id, timeout_seconds=3600, poll_interval_seconds=5.0) -> UIModelDeployment`: Poll an existing deployment until it reaches `DEPLOYED`; raises `DeploymentFailedError` on a FAILED/ERROR/MUST_REDOWNLOAD terminal status and `TimeoutError` past the deadline
- `list_deployments() -> List[ModelDeployment]`: List all deployments
- `list_active_deployments() -> List[UIModelDeployment]`: List only active deployments with running instances
- `get_deployment(deployment_id: UUID) -> ModelDeployment`: Get deployment details
- `stop_deployment(deployment_id: UUID)`: Stop a deployment
- `get_deployment_status(deployment_id: UUID) -> DeploymentStatus`: Get deployment status

```python
# Estimate VRAM requirements
vram_needed = client.serving.estimate_model_vram(model_id)

# Deploy a model (blocks until ready via client-side polling)
deployment_id = client.serving.deploy_model(repo_id="org/model-repo")

# Fire-and-forget deploy: get the id immediately, observe readiness later
deployment_id = client.serving.deploy_model(repo_id="org/model-repo", wait=False)
deployment = client.serving.wait_deployment_ready(deployment_id)

# List all deployments
deployments = client.serving.list_deployments()

# List only active deployments (deployed status with running instances)
active_deployments = client.serving.list_active_deployments()
# Each active deployment will have:
# - id: The deployment ID
# - m_id: The model ID
# - m_name: The model name
# - status: The deployment status
# - instances: List of running instances
# - lb_port: The load balancer port
# - endpoint: The HTTP endpoint for the deployment (e.g. http://hostname:port/v1)

# Get deployment status
status = client.serving.get_deployment_status(deployment_id)

# Stop deployment
client.serving.stop_deployment(deployment_id)
```

## Model Instance Management

### Available Methods
- `list_model_instances() -> List[ModelInstance]`: List all model instances
- `get_model_instance(instance_id: UUID) -> ModelInstance`: Get instance details
- `get_health(deployment_id: UUID) -> Dict[str, Any]`: Get deployment health
- `unload_model(deployment_id: UUID)`: Unload model from memory
- `load_model(deployment_id: UUID)`: Load model into memory

```python
# List model instances
instances = client.serving.list_model_instances()

# Get instance details
instance = client.serving.get_model_instance(instance_id)

# Check deployment health
health = client.serving.get_health(deployment_id)

# Load/Unload model
client.serving.unload_model(deployment_id)
client.serving.load_model(deployment_id)
```

## Error Handling
The service includes built-in error handling for common scenarios:
```python
from kamiwaza_sdk.exceptions import APIError, DeploymentFailedError

try:
    deployment_id = client.serving.deploy_model(repo_id="org/model-repo")
except DeploymentFailedError as e:
    # Terminal FAILED/ERROR/MUST_REDOWNLOAD status observed while waiting
    # for readiness (MUST_REDOWNLOAD = corrupted/incomplete model files).
    # e.deployment_id identifies the in-flight deployment for cleanup.
    print(f"Deployment failed ({e.status}, {e.last_error_code}): {e.last_error_message}")
except TimeoutError as e:
    # Also carries e.deployment_id so the stuck deployment can be stopped.
    print("Deployment did not become ready in time")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Always estimate VRAM requirements before deployment
2. Monitor deployment health regularly
3. Use appropriate number of replicas based on load
4. Implement proper error handling
5. Clean up unused deployments
6. Consider using advanced generation parameters for better control
7. Load/unload models to manage memory efficiently
