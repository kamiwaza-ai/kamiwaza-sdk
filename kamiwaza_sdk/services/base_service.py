# kamiwaza_sdk/services/base_service.py

class BaseService:
    def __init__(self, client):
        self.client = client