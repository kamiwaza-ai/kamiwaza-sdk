# Optimize SDK Performance

I need to optimize the performance of {OPERATION} which is currently {ISSUE_DESCRIPTION}.

## Performance Metrics

- **Current latency**: {CURRENT_METRICS}
- **Target latency**: {TARGET_METRICS}
- **Bottleneck**: {IDENTIFIED_BOTTLENECK}

## Investigation Steps

Please:
1. Profile the current implementation
2. Identify performance bottlenecks
3. Propose optimizations following SDK patterns
4. Implement changes incrementally
5. Measure improvement after each change

## Common SDK Performance Issues

### Lazy Loading Not Working
```python
# ❌ Wrong: Service initialized eagerly
class KamiwazaClient:
    def __init__(self, **kwargs):
        self.models = ModelsService(self._client)  # Eager

# ✅ Correct: Service initialized on access
class KamiwazaClient:
    @property
    def models(self):
        if not hasattr(self, '_models'):
            self._models = ModelsService(self._client)
        return self._models
```

### N+1 Query Pattern
```python
# ❌ Wrong: Multiple API calls
models = []
for model_id in model_ids:
    model = client.models.get(model_id)  # N API calls
    models.append(model)

# ✅ Correct: Batch operation
models = client.models.list(ids=model_ids)  # 1 API call
```

### Large Response Handling
```python
# ❌ Wrong: Load everything into memory
all_models = client.models.list(limit=10000)

# ✅ Correct: Use pagination
for page in client.models.iter_pages(per_page=100):
    for model in page.items:
        process(model)  # Process incrementally
```

### Connection Pooling
```python
# ✅ Use session for connection reuse
class BaseService:
    def __init__(self, client):
        self._session = requests.Session()
        self._session.headers.update(client.get_headers())
```

## Optimization Techniques

### 1. Request Optimization
- Batch multiple operations
- Use appropriate page sizes
- Implement request caching
- Reuse HTTP connections

### 2. Data Processing
- Stream large responses
- Use generators for iteration
- Process data incrementally
- Avoid loading all data into memory

### 3. Async Operations
```python
# For concurrent operations
import asyncio

async def download_multiple(model_ids):
    tasks = [download_model(mid) for mid in model_ids]
    return await asyncio.gather(*tasks)
```

### 4. Caching Strategy
```python
from functools import lru_cache

class ModelsService:
    @lru_cache(maxsize=128)
    def get_model_cached(self, model_id: str):
        return self.get(model_id)
```

## Measurement

### Before/After Timing
```python
import time

start = time.time()
result = operation()
duration = time.time() - start
print(f"Operation took {duration:.2f} seconds")
```

### Memory Profiling
```python
import tracemalloc

tracemalloc.start()
result = operation()
current, peak = tracemalloc.get_traced_memory()
print(f"Memory: current={current/1024/1024:.1f}MB, peak={peak/1024/1024:.1f}MB")
tracemalloc.stop()
```