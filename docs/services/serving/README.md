# Serving Service

## Overview
The Serving Service (`ServingService`) provides comprehensive model deployment and serving capabilities for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/serving.py`, this service manages Ray cluster operations, model deployment, and inference requests.

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
- `deploy_model(deployment: CreateModelDeployment) -> ModelDeployment`: Deploy a model
- `list_deployments() -> List[ModelDeployment]`: List all deployments
- `list_active_deployments() -> List[UIModelDeployment]`: List only active deployments with running instances
- `get_deployment(deployment_id: UUID) -> ModelDeployment`: Get deployment details
- `stop_deployment(deployment_id: UUID)`: Stop a deployment
- `get_deployment_status(deployment_id: UUID) -> DeploymentStatus`: Get deployment status

```python
# Estimate VRAM requirements
vram_needed = client.serving.estimate_model_vram(model_id)

# Deploy a model
deployment = client.serving.deploy_model(CreateModelDeployment(
    model_id=model_id,
    name="my-deployment",
    replicas=1,
    max_concurrent_requests=4
))

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
try:
    deployment = client.serving.deploy_model(deployment_config)
except DeploymentError as e:
    print(f"Deployment failed: {e}")
except ResourceError as e:
    print(f"Resource allocation failed: {e}")
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
