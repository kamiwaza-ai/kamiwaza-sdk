# kamiwaza_client/services/auth.py

from typing import Dict, List, Optional
from uuid import UUID
from .base_service import BaseService

class AuthService(BaseService):
    def login_for_access_token(self, username: str, password: str) -> Dict:
        """Login for access token."""
        data = {"username": username, "password": password}
        return self.client.post("/auth/token", data=data)

    def verify_token(self, authorization: Optional[str] = None) -> Dict:
        """Verify token."""
        headers = {"Authorization": authorization} if authorization else None
        return self.client.get("/auth/verify-token", headers=headers)

    def create_local_user(self, user_data: Dict) -> Dict:
        """Create a local user."""
        return self.client.post("/auth/users/local", json=user_data)

    def list_users(self) -> List[Dict]:
        """List all users."""
        return self.client.get("/auth/users/")

    def read_users_me(self, authorization: str) -> Dict:
        """Read current user's information."""
        headers = {"Authorization": authorization}
        return self.client.get("/auth/users/me/", headers=headers)

    def login_local(self, username: str, password: str) -> Dict:
        """Login locally."""
        params = {"username": username, "password": password}
        return self.client.post("/auth/local-login", params=params)

    def read_user(self, user_id: UUID) -> Dict:
        """Read a specific user."""
        return self.client.get(f"/auth/users/{user_id}")

    def update_user(self, user_id: UUID, user_data: Dict) -> Dict:
        """Update a user."""
        return self.client.put(f"/auth/users/{user_id}", json=user_data)

    def delete_user(self, user_id: UUID) -> None:
        """Delete a user."""
        return self.client.delete(f"/auth/users/{user_id}")

    def read_own_permissions(self, token: str) -> Dict:
        """Read own permissions."""
        params = {"token": token}
        return self.client.get("/auth/users/me/permissions", params=params)

    def create_organization(self, org_data: Dict) -> Dict:
        """Create an organization."""
        return self.client.post("/auth/organizations/", json=org_data)

    def read_organization(self, org_id: UUID) -> Dict:
        """Read an organization."""
        return self.client.get(f"/auth/organizations/{org_id}")

    def update_organization(self, org_id: UUID, org_data: Dict) -> Dict:
        """Update an organization."""
        return self.client.put(f"/auth/organizations/{org_id}", json=org_data)

    def delete_organization(self, org_id: UUID) -> None:
        """Delete an organization."""
        return self.client.delete(f"/auth/organizations/{org_id}")

    def create_group(self, group_data: Dict) -> Dict:
        """Create a group."""
        return self.client.post("/auth/groups/", json=group_data)

    def read_group(self, group_id: UUID) -> Dict:
        """Read a group."""
        return self.client.get(f"/auth/groups/{group_id}")

    def update_group(self, group_id: UUID, group_data: Dict) -> Dict:
        """Update a group."""
        return self.client.put(f"/auth/groups/{group_id}", json=group_data)

    def delete_group(self, group_id: UUID) -> None:
        """Delete a group."""
        return self.client.delete(f"/auth/groups/{group_id}")

    def create_role(self, role_data: Dict) -> Dict:
        """Create a role."""
        return self.client.post("/auth/roles/", json=role_data)

    def read_role(self, role_id: UUID) -> Dict:
        """Read a role."""
        return self.client.get(f"/auth/roles/{role_id}")

    def update_role(self, role_id: UUID, role_data: Dict) -> Dict:
        """Update a role."""
        return self.client.put(f"/auth/roles/{role_id}", json=role_data)

    def delete_role(self, role_id: UUID) -> None:
        """Delete a role."""
        return self.client.delete(f"/auth/roles/{role_id}")

    def create_right(self, right_data: Dict) -> Dict:
        """Create a right."""
        return self.client.post("/auth/rights/", json=right_data)

    def read_right(self, right_id: UUID) -> Dict:
        """Read a right."""
        return self.client.get(f"/auth/rights/{right_id}")

    def update_right(self, right_id: UUID, right_data: Dict) -> Dict:
        """Update a right."""
        return self.client.put(f"/auth/rights/{right_id}", json=right_data)

    def delete_right(self, right_id: UUID) -> None:
        """Delete a right."""
        return self.client.delete(f"/auth/rights/{right_id}")

    def add_user_to_group(self, user_id: UUID, group_id: UUID) -> None:
        """Add a user to a group."""
        return self.client.post(f"/auth/users/{user_id}/groups/{group_id}")

    def remove_user_from_group(self, user_id: UUID, group_id: UUID) -> None:
        """Remove a user from a group."""
        return self.client.delete(f"/auth/users/{user_id}/groups/{group_id}")

    def assign_role_to_group(self, group_id: UUID, role_id: UUID) -> None:
        """Assign a role to a group."""
        return self.client.post(f"/auth/groups/{group_id}/roles/{role_id}")

    def remove_role_from_group(self, group_id: UUID, role_id: UUID) -> None:
        """Remove a role from a group."""
        return self.client.delete(f"/auth/groups/{group_id}/roles/{role_id}")

    def assign_right_to_role(self, role_id: UUID, right_id: UUID) -> None:
        """Assign a right to a role."""
        return self.client.post(f"/auth/roles/{role_id}/rights/{right_id}")

    def remove_right_from_role(self, role_id: UUID, right_id: UUID) -> None:
        """Remove a right from a role."""
        return self.client.delete(f"/auth/roles/{role_id}/rights/{right_id}")