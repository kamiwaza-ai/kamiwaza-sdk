# kamiwaza_client/services/auth.py

from typing import List, Optional
from uuid import UUID
from ..schemas.auth import (
    Token, User, LocalUserCreate, UserUpdate, Organization, OrganizationCreate, OrganizationUpdate,
    Group, GroupCreate, GroupUpdate, Role, RoleCreate, RoleUpdate, Right, RightCreate, RightUpdate,
    UserPermissions
)
from .base_service import BaseService

class AuthService(BaseService):
    def login_for_access_token(
        self, 
        username: str, 
        password: str,
        grant_type: str = "password",
        scope: str = "",
        client_id: str = "string",
        client_secret: str = "string"
    ) -> Token:
        """Login for access token."""
        # Format data as form-encoded
        form_data = {
            "grant_type": grant_type,
            "username": username,
            "password": password,
            "scope": scope,
            "client_id": client_id,
            "client_secret": client_secret
        }
        
        # Make sure we're sending as form data
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        try:
            # Debug logging
            self.client.logger.debug(f"Attempting login with username: {username}")
            self.client.logger.debug(f"Form data: {form_data}")
            
            response = self.client.post(
                "/auth/token", 
                data=form_data,
                headers=headers
            )
            return Token.model_validate(response)
        except Exception as e:
            self.client.logger.error(f"Login failed: {str(e)}")
            raise


    def verify_token(self, authorization: Optional[str] = None):
        """Verify token."""
        # Extract token from Bearer string if present
        token = authorization.split(" ")[1] if authorization and " " in authorization else authorization
        
        cookies = {"access_token": token} if token else None
        response = self.client.get("/auth/verify-token", cookies=cookies)
        return User.model_validate(response)

    def create_local_user(self, user: LocalUserCreate) -> User:
        """Create a local user."""
        response = self.client.post("/auth/users/local", json=user.model_dump())
        return User.model_validate(response)

    def list_users(self) -> List[User]:
        """List all users."""
        response = self.client.get("/auth/users/")
        return [User.model_validate(user) for user in response]

    def read_users_me(self, authorization: str):
        """Read current user's information."""
        # Extract token from Bearer string if present
        token = authorization.split(" ")[1] if " " in authorization else authorization
        
        cookies = {"access_token": token}
        response = self.client.get("/auth/users/me", cookies=cookies)
        return response

    def login_local(self, username: str, password: str) -> Token:
        """Login locally."""
        params = {"username": username, "password": password}
        response = self.client.post("/auth/local-login", params=params)
        return Token.model_validate(response)

    def read_user(self, user_id: UUID) -> User:
        """Read a specific user."""
        response = self.client.get(f"/auth/users/{user_id}")
        return User.model_validate(response)

    def update_user(self, user_id: UUID, user: UserUpdate) -> User:
        """Update a user."""
        response = self.client.put(f"/auth/users/{user_id}", json=user.model_dump())
        return User.model_validate(response)

    def delete_user(self, user_id: UUID) -> None:
        """Delete a user."""
        self.client.delete(f"/auth/users/{user_id}")

    def read_own_permissions(self, token: str) -> UserPermissions:
        """Read own permissions."""
        params = {"token": token}
        response = self.client.get("/auth/users/me/permissions", params=params)
        return UserPermissions.model_validate(response)

    def create_organization(self, org: OrganizationCreate) -> Organization:
        """Create an organization."""
        response = self.client.post("/auth/organizations/", json=org.model_dump())
        return Organization.model_validate(response)

    def read_organization(self, org_id: UUID) -> Organization:
        """Read an organization."""
        response = self.client.get(f"/auth/organizations/{org_id}")
        return Organization.model_validate(response)

    def update_organization(self, org_id: UUID, org: OrganizationUpdate) -> Organization:
        """Update an organization."""
        response = self.client.put(f"/auth/organizations/{org_id}", json=org.model_dump())
        return Organization.model_validate(response)

    def delete_organization(self, org_id: UUID) -> None:
        """Delete an organization."""
        self.client.delete(f"/auth/organizations/{org_id}")

    def create_group(self, group: GroupCreate) -> Group:
        """Create a group."""
        response = self.client.post("/auth/groups/", json=group.model_dump())
        return Group.model_validate(response)

    def read_group(self, group_id: UUID) -> Group:
        """Read a group."""
        response = self.client.get(f"/auth/groups/{group_id}")
        return Group.model_validate(response)

    def update_group(self, group_id: UUID, group: GroupUpdate) -> Group:
        """Update a group."""
        response = self.client.put(f"/auth/groups/{group_id}", json=group.model_dump())
        return Group.model_validate(response)

    def delete_group(self, group_id: UUID) -> None:
        """Delete a group."""
        self.client.delete(f"/auth/groups/{group_id}")

    def create_role(self, role: RoleCreate) -> Role:
        """Create a role."""
        response = self.client.post("/auth/roles/", json=role.model_dump())
        return Role.model_validate(response)

    def read_role(self, role_id: UUID) -> Role:
        """Read a role."""
        response = self.client.get(f"/auth/roles/{role_id}")
        return Role.model_validate(response)

    def update_role(self, role_id: UUID, role: RoleUpdate) -> Role:
        """Update a role."""
        response = self.client.put(f"/auth/roles/{role_id}", json=role.model_dump())
        return Role.model_validate(response)

    def delete_role(self, role_id: UUID) -> None:
        """Delete a role."""
        self.client.delete(f"/auth/roles/{role_id}")

    def create_right(self, right: RightCreate) -> Right:
        """Create a right."""
        response = self.client.post("/auth/rights/", json=right.model_dump())
        return Right.model_validate(response)

    def read_right(self, right_id: UUID) -> Right:
        """Read a right."""
        response = self.client.get(f"/auth/rights/{right_id}")
        return Right.model_validate(response)

    def update_right(self, right_id: UUID, right: RightUpdate) -> Right:
        """Update a right."""
        response = self.client.put(f"/auth/rights/{right_id}", json=right.model_dump())
        return Right.model_validate(response)

    def delete_right(self, right_id: UUID) -> None:
        """Delete a right."""
        self.client.delete(f"/auth/rights/{right_id}")

    def add_user_to_group(self, user_id: UUID, group_id: UUID) -> None:
        """Add a user to a group."""
        self.client.post(f"/auth/users/{user_id}/groups/{group_id}")

    def remove_user_from_group(self, user_id: UUID, group_id: UUID) -> None:
        """Remove a user from a group."""
        self.client.delete(f"/auth/users/{user_id}/groups/{group_id}")

    def assign_role_to_group(self, group_id: UUID, role_id: UUID) -> None:
        """Assign a role to a group."""
        self.client.post(f"/auth/groups/{group_id}/roles/{role_id}")

    def remove_role_from_group(self, group_id: UUID, role_id: UUID) -> None:
        """Remove a role from a group."""
        self.client.delete(f"/auth/groups/{group_id}/roles/{role_id}")

    def assign_right_to_role(self, role_id: UUID, right_id: UUID) -> None:
        """Assign a right to a role."""
        self.client.post(f"/auth/roles/{role_id}/rights/{right_id}")

    def remove_right_from_role(self, role_id: UUID, right_id: UUID) -> None:
        """Remove a right from a role."""
        self.client.delete(f"/auth/roles/{role_id}/rights/{right_id}")