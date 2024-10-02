from kamiwaza_client import KamiwazaClient

# Initialize the client
client = KamiwazaClient("https://api.kamiwaza.ai", api_key="your_api_key_here")

# List models
models = client.models.list_models()
print("Available models:", models)

# Create a new model
new_model = client.models.create_model("My Test Model", description="A test model created via the SDK")
print("Created model:", new_model)

# Get a specific model
model_id = new_model['id']
model_details = client.models.get_model(model_id)
print("Model details:", model_details)

# Delete the model
client.models.delete_model(model_id)
print(f"Model {model_id} deleted")