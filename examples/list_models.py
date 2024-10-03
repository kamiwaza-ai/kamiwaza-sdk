# examples/list_models.py

from kamiwaza_client import KamiwazaClient

def main():
    # Initialize the client
    client = KamiwazaClient("http://localhost:7777/api")

    # List all models
    try:
        models = client.models.list_models(load_files=False)
        print("Models:")
        for model in models:
            print(f"- {model.name} (ID: {model.id})")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()