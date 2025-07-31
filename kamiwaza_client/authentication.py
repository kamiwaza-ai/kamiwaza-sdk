# kamiwaza_client/authentication.py

from abc import ABC, abstractmethod
from typing import Optional
import requests
from datetime import datetime, timedelta
from .schemas.auth import Token
from .exceptions import AuthenticationError

class Authenticator(ABC):
    @abstractmethod
    def authenticate(self, session: requests.Session):
        pass

    @abstractmethod
    def refresh_token(self, session: requests.Session):
        pass

class ApiKeyAuthenticator(Authenticator):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def authenticate(self, session: requests.Session):
        session.headers.update({'Authorization': f'Bearer {self.api_key}'})

    def refresh_token(self, session: requests.Session):
        # API keys generally do not expire, so no refresh needed
        pass

class UserPasswordAuthenticator(Authenticator):
    def __init__(self, username: str, password: str, auth_service):
        self.username = username
        self.password = password
        self.auth_service = auth_service
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def authenticate(self, session: requests.Session):
        if not self.token or self.token_expiry is None or datetime.utcnow() >= self.token_expiry:
            self.refresh_token(session)
        # Set both Authorization header and cookie for compatibility
        session.headers.update({'Authorization': f'Bearer {self.token}'})
        session.cookies.set('access_token', self.token)
        # Debug log
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Set Authorization header and cookie: Bearer {self.token[:20]}...")

    def refresh_token(self, session: requests.Session):
        try:
            token_response: Token = self.auth_service.login_for_access_token(self.username, self.password)
            self.token = token_response.access_token
            self.token_expiry = datetime.utcnow() + timedelta(seconds=token_response.expires_in)
            session.headers.update({'Authorization': f'Bearer {self.token}'})
        except Exception as e:
            raise AuthenticationError(f"Failed to refresh token: {str(e)}")

class OAuthAuthenticator(Authenticator):
    def __init__(self, client_id: str, client_secret: str, auth_service):
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_service = auth_service
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def authenticate(self, session: requests.Session):
        if not self.token or datetime.utcnow() >= self.token_expiry:
            self.refresh_token(session)
        session.headers.update({'Authorization': f'Bearer {self.token}'})

    def refresh_token(self, session: requests.Session):
        # Placeholder for OAuth token retrieval logic
        # This will involve redirecting the user to the authorization URL,
        # obtaining the authorization code, and exchanging it for an access token.
        raise NotImplementedError("OAuth authentication is not yet implemented.")
