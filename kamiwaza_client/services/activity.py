# kamiwaza_client/services/activity.py

from typing import List, Dict
from .base_service import BaseService

class ActivityService(BaseService):
    def get_recent_activity(self) -> List[Dict]:
        """Get recent activity."""
        return self.client.get("/activity/activities/")