# Core Engineering Principles for Kamiwaza SDK

## The Prime Directives
1. **Think First, Code Second** - Always plan before implementing
2. **One Task at a Time** - Complete vertically before moving horizontally  
3. **Search Before Creating** - Existing code is your friend
4. **Simple Beats Clever** - YAGNI, KISS, and boring solutions win
5. **Tests Are Mandatory** - No exceptions, no "fix it later"

## YAGNI (You Aren't Gonna Need It)
Only build what's required RIGHT NOW. Don't add features "just in case."

### SDK Examples
```python
# ❌ Wrong: Adding unnecessary abstractions
class AbstractModelDownloader(ABC):
    """We only have one downloader, this is premature"""
    
# ✅ Correct: Direct implementation
class ModelDownloader:
    """Simple, works, can abstract later if needed"""
```

## KISS (Keep It Simple, Stupid)
The best solution is the most boring solution. If you can't explain it in one sentence, it's too complex.

### SDK Examples
```python
# ❌ Wrong: Over-engineered
def get_model(self, id: str) -> Model:
    return self._execute_with_retry(
        lambda: self._cache.get_or_fetch(
            id, lambda: self._fetch_with_transform(id)
        )
    )

# ✅ Correct: Simple and clear
def get_model(self, id: str) -> Model:
    response = self._request("GET", f"/models/{id}")
    return Model(**response)
```

## The Rule of Three (DRY with Discipline)
1. First occurrence: Write it
2. Second occurrence: Copy it, note duplication  
3. Third occurrence: Extract to shared function

### SDK Example
```python
# First service: Write auth header logic
headers = {"Authorization": f"Bearer {self.token}"}

# Second service: Copy it, note duplication
headers = {"Authorization": f"Bearer {self.token}"}  # TODO: Duplicated

# Third service: Now extract
def _get_auth_headers(self) -> dict:
    return {"Authorization": f"Bearer {self.token}"}
```

## Walking Skeleton (Incremental Development)
Start with the thinnest working version. Add flesh incrementally.

### SDK Development Flow
```python
# Step 1: Skeleton - Just make it work
def download_model(self, model_id: str) -> str:
    url = f"{self.base_url}/models/{model_id}/download"
    response = requests.get(url)
    return response.json()["download_url"]

# Step 2: Add error handling
def download_model(self, model_id: str) -> str:
    try:
        response = self._request("GET", f"/models/{model_id}/download")
        return response["download_url"]
    except HTTPError as e:
        if e.response.status_code == 404:
            raise ModelNotFoundError(f"Model {model_id} not found")
        raise

# Step 3: Add progress tracking (only after basics work)
def download_model(self, model_id: str, progress_callback=None) -> str:
    # ... existing logic plus progress
```

## Vertical Slices Only
Complete one feature fully before starting another.

### SDK Example
```
✅ Correct: Complete models.get() fully
1. Implement ModelsService.get()
2. Add Model schema
3. Add error handling  
4. Write tests
5. Document method

❌ Wrong: Implement all methods partially
1. Stub out all ModelsService methods
2. Then add schemas for everything
3. Then add tests for everything
```

## Checkpoint Every 30 Minutes
Regular verification prevents drift:
```bash
pwd                    # Still in right directory?
make test             # Tests still passing? (or: uv run pytest)
git diff              # What changed?
# Am I still on task?
```

## Scope Creep Prevention
When tempted to add "improvements":
```
STOP. 
1. Complete current task
2. Document improvement separately
3. Get approval before implementing
```

### SDK Example
```python
# Task: Add model download
# Temptation: "While I'm here, let me add caching"
# Action: STOP. Complete download first. Note caching idea.
```

## Recovery Procedures

### When Lost in Code
```bash
# STOP and reorient
pwd
git status
git diff
# Reread task definition
# Confirm next action
```

### When Tests Fail
```bash
# STOP and isolate
git diff HEAD  # What changed?
git stash      # Save work
git checkout HEAD~1  # Go to last working state
# Apply changes incrementally
```

### When Overengineering
1. Comment out abstractions
2. Write direct implementation
3. Verify it works
4. Only add complexity if truly needed

## Red Flags - Stop Immediately

### Phrases That Signal Problems
- "Let me create a base class for future use"
- "I'll implement all endpoints first"
- "While I'm here, I'll also..."
- "I'll fix the tests later"
- "This might be useful someday"

### Actions That Signal Problems
- Creating interfaces with single implementation
- Adding features not in requirements
- Skipping tests to "save time"
- Refactoring working code without reason
- Adding abstraction layers "just in case"

## Quality Gates

### Cannot Start Coding Until
1. ✅ Task defined clearly
2. ✅ Existing code searched
3. ✅ Test approach planned
4. ✅ Success criteria clear

### Must Stop and Review If
- Method exceeds 50 lines
- Class exceeds 300 lines
- Can't explain purpose in one sentence
- Tests start failing
- Scope expanding beyond original task

### Cannot Complete Task Until
1. ✅ Tests written and passing
2. ✅ Type hints complete
3. ✅ Error handling in place
4. ✅ Original requirement met (nothing more)
5. ✅ Code formatted (black/isort)

## Daily Affirmations
- Boring code is good code
- Working code beats clever code
- Simple today, refactor tomorrow (if needed)
- YAGNI until YAGNI
- Tests are not optional