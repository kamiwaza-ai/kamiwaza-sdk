 # OpenAI Service

## Overview
The OpenAI Service (`OpenAIService`) provides OpenAI API compatibility for Kamiwaza model deployments. This service allows you to use your Kamiwaza-deployed models with the familiar OpenAI Python SDK interface.

## Key Features
- OpenAI API compatibility
- Automatic deployment discovery
- Configurable SSL verification
- Support for multiple targeting methods (model name, deployment ID, or direct endpoint)

## Usage

### Getting an OpenAI Client

The service provides several ways to get an OpenAI client for your deployments:

```python
# Initialize Kamiwaza client
client = KamiwazaClient("https://prod.kamiwaza.ai/api/")

# Get client by model name
openai_client = client.openai.get_client(model="Owen2.5-72B-Instruct-GPTQ-Int4")

# Get client by deployment ID
openai_client = client.openai.get_client(deployment_id="your-deployment-uuid")

# Get client by direct endpoint
openai_client = client.openai.get_client(endpoint="http://your-endpoint:port/v1")
```

### Using the OpenAI Client

Once you have an OpenAI client, you can use it just like you would use the official OpenAI SDK:

```python
# Create a chat completion
response = openai_client.chat.completions.create(
    messages=[
        {"role": "user", "content": "Say hello in Chinese!"}
    ],
    model="model"   
)

# Access the response
print(response.choices[0].message.content)
```

## Best Practices
1. Use model name targeting when you want automatic failover between multiple deployments of the same model
2. Use deployment ID targeting when you need to ensure you're using a specific deployment
3. Use direct endpoint targeting when you need to bypass deployment discovery
4. Keep the same SSL verification settings as your main Kamiwaza client for consistency

## Error Handling

```python
try:
    openai_client = client.openai.get_client(model="my-model")
except ValueError as e:
    print(f"No active deployment found: {e}")
except APIError as e:
    print(f"API error occurred: {e}")
```

## Notes
- The OpenAI client is configured with `api_key="local"` since authentication is handled by the Kamiwaza client
- SSL verification settings are inherited from your Kamiwaza client configuration
- The model parameter in OpenAI API calls is ignored since the model is determined by the deployment
- All standard OpenAI SDK features (retries, timeouts, etc.) are availables