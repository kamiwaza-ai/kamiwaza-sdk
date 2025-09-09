# Successful Service Patterns in Kamiwaza SDK

## Lazy-Loading Services

The SDK's lazy-loading pattern has proven highly effective:

```python
class KamiwazaClient:
    @property
    def models(self) -> ModelsService:
        if not hasattr(self, '_models'):
            self._models = ModelsService(self._client)
        return self._models
```

### Why It Works
1. **Reduced startup time** - Services only initialized when needed
2. **Lower memory footprint** - Unused services never instantiated
3. **Clean API** - Accessing services feels natural: `client.models.list()`
4. **Single instance guarantee** - Each service created once per client

## Mixin Composition for Complex Services

The models service successfully uses mixins to organize ~20 methods:

```python
class ModelsService(
    BaseService,
    SearchMixin,
    DownloadMixin, 
    FileMixin,
    ConfigMixin,
    CompatibilityMixin
):
    pass
```

### Benefits Observed
- Each mixin stays focused (50-100 lines)
- Easy to find functionality
- Simple to test in isolation
- New features added without touching existing code

## Progress Tracking with Context Managers

Download operations use context managers for clean progress tracking:

```python
with DownloadTracker(total_size=file_size) as tracker:
    for chunk in response.iter_content(chunk_size=8192):
        file.write(chunk)
        tracker.update(len(chunk))
```

### Why This Pattern Succeeds
- Automatic cleanup on errors
- Works with or without TTY
- Easy to disable for CI/CD
- Minimal performance overhead

## Pydantic for API Contracts

Using Pydantic models for all API interactions has prevented numerous bugs:

```python
class Model(BaseModel):
    id: str
    name: str = Field(..., min_length=1)
    created_at: datetime
    
    class Config:
        extra = "allow"  # Forward compatibility
```

### Benefits in Production
1. **Automatic validation** catches API changes
2. **IDE autocomplete** improves developer experience
3. **Forward compatibility** with `extra = "allow"`
4. **Automatic JSON serialization** for requests

## Centralized Authentication

Having `AuthenticationManager` handle all auth concerns works well:

```python
class AuthenticationManager:
    def get_headers(self) -> dict:
        if self.api_key:
            return {"X-API-Key": self.api_key}
        elif self.token:
            return {"Authorization": f"Bearer {self.token}"}
```

### Advantages
- Single place to update auth logic
- Easy to add new auth methods
- Automatic token refresh hidden from services
- Consistent error handling

## Error Hierarchy

The exception hierarchy maps HTTP errors to semantic exceptions:

```python
try:
    response = self._request(...)
except HTTPError as e:
    if e.response.status_code == 404:
        raise ResourceNotFoundError(...)
    elif e.response.status_code == 401:
        raise AuthenticationError(...)
```

### Why It Works
- Callers can catch specific errors
- Error messages are user-friendly
- Original context preserved
- Easy to handle errors appropriately

## Request Retry Logic

The SDK implements smart retry logic in `BaseService`:

```python
def _request_with_retry(self, method, url, **kwargs):
    for attempt in range(self.max_retries):
        try:
            return self._request(method, url, **kwargs)
        except (Timeout, ConnectionError) as e:
            if attempt == self.max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # Exponential backoff
```

### Success Factors
- Only retries transient errors
- Exponential backoff prevents thundering herd
- Configurable max retries
- Preserves original error on final failure

## Type-Safe Configuration

Using Pydantic Settings for configuration has prevented many bugs:

```python
class SDKConfig(BaseSettings):
    api_key: Optional[str] = None
    base_url: str = "https://api.kamiwaza.ai"
    timeout: int = 30
    
    class Config:
        env_prefix = "KAMIWAZA_"
```

### Benefits
- Environment variables automatically loaded
- Type validation at startup
- Clear documentation of all settings
- Easy to test with different configs