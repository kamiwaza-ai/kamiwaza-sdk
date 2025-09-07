# Common SDK Development Pitfalls

## Returning Raw Dictionaries Instead of Pydantic Models

### The Problem
Early versions returned raw API responses:
```python
# ❌ Bad: Returns dict, no validation
def get_model(self, model_id: str) -> dict:
    return self._request("GET", f"/models/{model_id}")
```

### What Went Wrong
- No IDE autocomplete
- No runtime validation
- API changes broke client code silently
- Type hints were lies

### The Fix
```python
# ✅ Good: Returns validated Pydantic model
def get_model(self, model_id: str) -> Model:
    response = self._request("GET", f"/models/{model_id}")
    return Model(**response)
```

## Forgetting Forward Compatibility

### The Problem
Strict Pydantic models broke when API added fields:
```python
# ❌ Bad: Breaks when API adds new fields
class Model(BaseModel):
    id: str
    name: str
    # No Config, so extra fields cause errors
```

### What Happened
- API added `description` field
- All SDK calls started failing with ValidationError
- Required emergency SDK update

### The Fix
```python
# ✅ Good: Allows extra fields
class Model(BaseModel):
    id: str
    name: str
    
    class Config:
        extra = "allow"  # Future-proof
```

## Circular Import Issues

### The Problem
Services importing from client module:
```python
# ❌ In services/models.py
from kamiwaza_client.client import KamiwazaClient  # Circular!

class ModelsService:
    def __init__(self, client: KamiwazaClient):
        self.client = client
```

### Why It Failed
- `client.py` imports `ModelsService`
- `ModelsService` imports `KamiwazaClient`
- Python import fails

### The Fix
```python
# ✅ Good: Type hints only, no runtime import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kamiwaza_client.client import KamiwazaClient

class ModelsService:
    def __init__(self, client: "KamiwazaClient"):
        self.client = client
```

## Mutable Default Arguments

### The Problem
```python
# ❌ Bad: Mutable default
def list_models(self, tags: list = []):
    tags.append("user-tag")  # Modifies shared list!
    return self._request("GET", "/models", params={"tags": tags})
```

### What Went Wrong
- All calls shared the same list
- Tags accumulated across calls
- Extremely hard to debug

### The Fix
```python
# ✅ Good: None default
def list_models(self, tags: Optional[List[str]] = None):
    if tags is None:
        tags = []
    return self._request("GET", "/models", params={"tags": tags})
```

## Not Handling Pagination

### The Problem
```python
# ❌ Bad: Assumes all results fit in one page
def list_all_models(self) -> List[Model]:
    response = self._request("GET", "/models")
    return [Model(**m) for m in response["items"]]
```

### What Happened
- Worked in dev with 10 models
- Failed in prod with 1000+ models
- Only returned first 20 results

### The Fix
```python
# ✅ Good: Handle pagination
def list_all_models(self) -> List[Model]:
    all_models = []
    page = 1
    while True:
        response = self._request("GET", "/models", params={"page": page})
        all_models.extend([Model(**m) for m in response["items"]])
        if len(all_models) >= response["total"]:
            break
        page += 1
    return all_models
```

## Synchronous File Operations in Async Context

### The Problem
```python
# ❌ Bad: Blocks event loop
async def download_model(self, model_id: str, path: Path):
    content = await self._async_request("GET", f"/models/{model_id}/download")
    path.write_bytes(content)  # Blocks!
```

### The Issue
- Blocked entire async event loop
- Other concurrent operations stalled
- Performance degraded severely

### The Fix
```python
# ✅ Good: Use async file I/O
import aiofiles

async def download_model(self, model_id: str, path: Path):
    content = await self._async_request("GET", f"/models/{model_id}/download")
    async with aiofiles.open(path, 'wb') as f:
        await f.write(content)
```

## Memory Issues with Large Downloads

### The Problem
```python
# ❌ Bad: Loads entire file into memory
def download_file(self, url: str, path: Path):
    response = requests.get(url)
    path.write_bytes(response.content)  # Could be GBs!
```

### What Went Wrong
- 2GB model file caused OOM
- Process crashed
- No progress indication

### The Fix
```python
# ✅ Good: Stream in chunks
def download_file(self, url: str, path: Path):
    with requests.get(url, stream=True) as response:
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
```

## Race Conditions in Token Refresh

### The Problem
```python
# ❌ Bad: Multiple threads refresh simultaneously
def _ensure_authenticated(self):
    if self._token_expired():
        self._refresh_token()  # Race condition!
```

### What Happened
- Thread A sees token expired, starts refresh
- Thread B sees token expired, starts refresh
- Double refresh causes errors

### The Fix
```python
# ✅ Good: Use lock
import threading

class AuthManager:
    def __init__(self):
        self._refresh_lock = threading.Lock()
    
    def _ensure_authenticated(self):
        if self._token_expired():
            with self._refresh_lock:
                # Check again inside lock
                if self._token_expired():
                    self._refresh_token()
```