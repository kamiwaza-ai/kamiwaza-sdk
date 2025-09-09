# Fix Failing Test

I have a failing test in `{TEST_FILE}` with this error:
```
{ERROR_OUTPUT}
```

## Investigation Steps

Please:
1. Read the failing test to understand what it's testing
2. Check the implementation being tested
3. Determine if the issue is:
   - Test is outdated (implementation changed)
   - Implementation has a bug
   - Mock setup is incorrect
   - Schema validation issue

## Common SDK Test Issues

### Mock Setup Problems
```python
# ❌ Wrong: Mock returns wrong structure
mock_client.request.return_value = ["item1", "item2"]

# ✅ Correct: Return dict matching API response
mock_client.request.return_value = {
    "items": [{"id": "1", "name": "item1"}],
    "total": 2
}
```

### Schema Validation Errors
```python
# ❌ Wrong: Missing required fields
response = {"name": "Test"}  # Missing 'id' field

# ✅ Correct: Include all required fields
response = {"id": "123", "name": "Test"}
```

### Async Test Issues
```python
# ❌ Wrong: Not awaiting async method
result = service.async_method()

# ✅ Correct: Await or use pytest-asyncio
result = await service.async_method()
```

## Fix Approach

Based on the error type:
1. **AssertionError**: Check test expectations vs actual behavior
2. **ValidationError**: Ensure mock data matches Pydantic schema
3. **AttributeError**: Verify mock setup and method names
4. **ImportError**: Check module paths and dependencies

## Verification

After fixing:
1. Run the specific test: `pytest {TEST_FILE}::{test_name} -v`
2. Run related tests: `pytest {TEST_DIRECTORY} -k {PATTERN}`
3. Ensure no regression: `pytest`