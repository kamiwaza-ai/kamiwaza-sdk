# kamiwaza_sdk/config.py

from . import __version__

# all of this is just to have something to start with... idk if we have versioning on the api etc
# also need to clarify what the base url is

# Default API endpoint
DEFAULT_API_BASE_URL = "http://localhost:7777"

# Default timeout for API requests (in seconds)
DEFAULT_TIMEOUT = 30

# Maximum retries for failed requests
MAX_RETRIES = 3

# User agent for API requests
USER_AGENT = f"KamiwazaClient/{__version__}"

# Default pagination limits
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000

# API version
API_VERSION = "v1"

# Endpoints
ENDPOINTS = {
    "models": f"/{API_VERSION}/models",
    "serving": f"/{API_VERSION}/serving",
    "vectordb": f"/{API_VERSION}/vectordb",
    "catalog": f"/{API_VERSION}/catalog",
    "embedding": f"/{API_VERSION}/embedding",
    "cluster": f"/{API_VERSION}/cluster",
    "activity": f"/{API_VERSION}/activity",
    "lab": f"/{API_VERSION}/lab",
    "auth": f"/{API_VERSION}/auth",
}