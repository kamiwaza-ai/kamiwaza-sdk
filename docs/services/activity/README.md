# Activity Service

## Overview
The Activity Service (`ActivityService`) provides comprehensive activity tracking and monitoring functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/activity.py`, this service handles the tracking and retrieval of user and system activities across the platform.

## Key Features
- Activity Tracking
- Recent Activity Retrieval
- Activity Filtering
- Timeline Management

## Activity Management

### Available Methods
- `get_recent_activity(limit: int = 50, skip: int = 0) -> List[Activity]`: Get recent activities

```python
# Get recent activities
activities = client.activity.get_recent_activity(
    limit=10,  # Number of activities to retrieve
    skip=0     # Number of activities to skip
)

# Process activities
for activity in activities:
    print(f"Activity: {activity.type}")
    print(f"User: {activity.user_id}")
    print(f"Timestamp: {activity.timestamp}")
    print(f"Details: {activity.details}")
```

## Integration with Other Services
The Activity Service works in conjunction with:
1. Authentication Service
   - For user identification
2. Lab Service
   - For lab activity tracking
3. Model Service
   - For model operation tracking
4. Cluster Service
   - For infrastructure events

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    activities = client.activity.get_recent_activity()
except PermissionError:
    print("Insufficient permissions")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Set appropriate activity limits
2. Implement activity filtering
3. Regular activity monitoring
4. Handle pagination properly
5. Process activities asynchronously
6. Implement proper error handling
7. Monitor activity patterns
8. Archive old activities

## Performance Considerations
- Pagination impact on retrieval time
- Activity log size
- Query performance
- Storage requirements

## Activity Types
The service tracks various types of activities:
1. User Actions
   - Login attempts
   - Resource creation
   - Configuration changes
2. System Events
   - Service status changes
   - Resource allocation
   - Error occurrences
3. Resource Operations
   - Model deployments
   - Lab creation/deletion
   - Data ingestion
