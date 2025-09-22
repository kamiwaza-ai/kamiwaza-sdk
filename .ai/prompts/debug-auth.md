# Debug Authentication Issue

I'm experiencing an authentication issue with the SDK. The error is:
```
{ERROR_MESSAGE}
```

## Context

- **Authentication Method**: [API Key / OAuth]
- **Environment**: [Production / Development]
- **SDK Version**: {VERSION}

## Investigation Steps

Please help me:
1. Check the AuthenticationManager setup
2. Verify credential format and validity
3. Review request headers being sent
4. Check for token expiration (OAuth)
5. Identify any recent changes

## Common Authentication Issues

### API Key Issues
```python
# ❌ Wrong: Using Bearer format for API key
client = KamiwazaClient(api_key="Bearer sk-...")

# ✅ Correct: Just the key
client = KamiwazaClient(api_key="sk-...")

# ❌ Wrong: Wrong header name
headers = {"Authorization": api_key}

# ✅ Correct: SDK uses X-API-Key
headers = {"X-API-Key": api_key}
```

### OAuth Token Issues
```python
# Check token expiration
if auth_manager.token_expired():
    auth_manager.refresh_token()

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Environment Variable Issues
```bash
# ❌ Wrong: Quotes in .env file
KAMIWAZA_API_KEY="sk-12345"

# ✅ Correct: No quotes
KAMIWAZA_API_KEY=sk-12345
```

## Debug Helpers

### Log Request Headers
```python
# Enable request logging
import requests
import logging

logging.basicConfig(level=logging.DEBUG)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
```

### Test Authentication
```python
# Minimal test to verify auth
client = KamiwazaClient(api_key="your-key")
try:
    models = client.models.list(limit=1)
    print("Auth successful!")
except AuthenticationError as e:
    print(f"Auth failed: {e}")
```

## Resolution Steps

1. Verify credentials are correct
2. Check credential format matches expected
3. Ensure environment variables are loaded
4. Test with curl to isolate SDK issues
5. Enable debug logging for more details