# kamiwaza_sdk/services/activity.py

from typing import List
from ..schemas.activity import Activity
from .base_service import BaseService

class ActivityService(BaseService):
    def get_recent_activity(self) -> List[Activity]:
        """Get recent activity."""
        response = self.client.get("/activity/activities/")
        return [Activity.model_validate(item) for item in response]