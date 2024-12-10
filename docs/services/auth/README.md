# Authentication Service

## Overview
The Authentication Service (`AuthService`) provides comprehensive user authentication and authorization functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/auth.py`, this service handles user management, token-based authentication, and role-based access control (RBAC).

## Key Features
- User Authentication (token-based)
- Local User Management
- Organization Management
- Role-Based Access Control (RBAC)
- Group Management
- Permission Management

## Authentication Methods

### Token-Based Authentication
```python
# Login to get access token
token = client.auth.login_for_access_token(
    username="user@example.com",
    password="secure_password"
)

# Verify token
user = client.auth.verify_token(authorization="Bearer <token>")
```

### Local Authentication
```python
# Create local user
user = client.auth.create_local_user(LocalUserCreate(
    username="newuser",
    email="user@example.com",
    password="securepass"
))

# Login locally
token = client.auth.login_local(username="newuser", password="securepass")
```

## User Management

### Available Methods
- `create_local_user(user: LocalUserCreate) -> User`: Create a new local user
- `list_users() -> List[User]`: List all users in the system
- `read_users_me(authorization: str)`: Get current user's information
- `read_user(user_id: UUID) -> User`: Get specific user information
- `update_user(user_id: UUID, user: UserUpdate) -> User`: Update user details
- `delete_user(user_id: UUID)`: Delete a user
- `read_own_permissions(token: str) -> UserPermissions`: Get current user's permissions

## Organization Management

### Available Methods
- `create_organization(org: OrganizationCreate) -> Organization`: Create new organization
- `read_organization(org_id: UUID) -> Organization`: Get organization details
- `update_organization(org_id: UUID, org: OrganizationUpdate) -> Organization`: Update organization
- `delete_organization(org_id: UUID)`: Delete an organization

## Group Management

### Available Methods
- `create_group(group: GroupCreate) -> Group`: Create new group
- `read_group(group_id: UUID) -> Group`: Get group details
- `update_group(group_id: UUID, group: GroupUpdate) -> Group`: Update group
- `delete_group(group_id: UUID)`: Delete a group
- `add_user_to_group(user_id: UUID, group_id: UUID)`: Add user to group
- `remove_user_from_group(user_id: UUID, group_id: UUID)`: Remove user from group

## Role Management

### Available Methods
- `create_role(role: RoleCreate) -> Role`: Create new role
- `read_role(role_id: UUID) -> Role`: Get role details
- `update_role(role_id: UUID, role: RoleUpdate) -> Role`: Update role
- `delete_role(role_id: UUID)`: Delete a role
- `assign_role_to_group(group_id: UUID, role_id: UUID)`: Assign role to group
- `remove_role_from_group(group_id: UUID, role_id: UUID)`: Remove role from group

## Rights Management

### Available Methods
- `create_right(right: RightCreate) -> Right`: Create new right
- `read_right(right_id: UUID) -> Right`: Get right details
- `update_right(right_id: UUID, right: RightUpdate) -> Right`: Update right
- `delete_right(right_id: UUID)`: Delete a right
- `assign_right_to_role(role_id: UUID, right_id: UUID)`: Assign right to role
- `remove_right_from_role(role_id: UUID, right_id: UUID)`: Remove right from role

## Error Handling
The service includes built-in error handling for common authentication scenarios:
```python
try:
    token = client.auth.login_for_access_token(username="user", password="pass")
except AuthenticationError:
    # Handle authentication failures
except APIError as e:
    # Handle API errors
    print(f"Operation failed: {e}")
```

## Best Practices
1. Always use secure passwords and handle credentials securely
2. Implement proper token management (storage and refresh)
3. Use role-based access control for granular permissions
4. Regular audit of user permissions and access rights
5. Clean up unused users, groups, and roles
