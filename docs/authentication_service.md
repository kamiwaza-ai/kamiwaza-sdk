# Authentication Service

The Authentication Service handles user authentication, user management, and permissions.

## Methods

### login_for_access_token(username: str, password: str)

Obtains an access token for the given username and password.

```python
token = client.auth.login_for_access_token("user@example.com", "password123")
```

### verify_token(authorization: str)

Verifies the validity of a token.

```python
is_valid = client.auth.verify_token("Bearer your_token_here")
```

### create_local_user(user: LocalUserCreate)

Creates a new local user.

```python
new_user = LocalUserCreate(username="newuser", email="newuser@example.com", password="securepassword")
created_user = client.auth.create_local_user(new_user)
```

### list_users()

Retrieves a list of all users.

```python
users = client.auth.list_users()
```

### read_users_me(authorization: str)

Gets information about the current user.

```python
current_user = client.auth.read_users_me("Bearer your_token_here")
```

### login_local(username: str, password: str)

Logs in with local credentials.

```python
login_result = client.auth.login_local("user@example.com", "password123")
```

### read_user(user_id: UUID)

Retrieves a specific user's details.

```python
user = client.auth.read_user("user_id_here")
```

### update_user(user_id: UUID, user: UserUpdate)

Updates user information.

```python
update_data = UserUpdate(full_name="New Name")
updated_user = client.auth.update_user("user_id_here", update_data)
```

### delete_user(user_id: UUID)

Deletes a user.

```python
client.auth.delete_user("user_id_here")
```

### read_own_permissions(token: str)

Gets the permissions of the current user.

```python
permissions = client.auth.read_own_permissions("Bearer your_token_here")
```
