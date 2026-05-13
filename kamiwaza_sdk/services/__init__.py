from .activity import ActivityService
from .apps import AppService
from .auth import AuthService
from .authz import AuthzService
from .catalog import CatalogService
from .cluster import ClusterService
from .cluster_federation import ClusterAPI
from .context import ContextService
from .datasets import DatasetsAPI
from .embedding import EmbeddingService
from .enclaves import EnclavesService
from .federations import FederationProxy, FederationsAPI, FederationUsersAPI
from .gates import GatesAPI
from .ingestion import IngestionService
from .jobs_federation import JobsAPI
from .lab import LabService
from .models import ModelService
from .prompts import PromptsService
from .retrieval import RetrievalService
from .retrieval_federation import RetrievalAPI
from .serving import ServingService
from .skills import SkillsService
from .subjects import SubjectGrantsAPI, SubjectsAPI
from .tools import ToolService

__all__ = [
    "ActivityService",
    "AppService",
    "AuthService",
    "AuthzService",
    "CatalogService",
    # WS-M3.2 federation-aware service surface (design v0.3.7 §4.2.11).
    # Aliased separately from the legacy ClusterService / RetrievalService
    # because ClusterAPI / RetrievalAPI inherit from them with extra M3
    # methods. Customer code uses ``client.cluster`` / ``client.retrieval``
    # (which resolves to the *APIs); direct import is here for tests and
    # advanced callers per PR-feedback M7 (architecture consistency).
    "ClusterAPI",
    "ClusterService",
    "ContextService",
    "DatasetsAPI",
    "EmbeddingService",
    "EnclavesService",
    "FederationProxy",
    "FederationUsersAPI",
    "FederationsAPI",
    "GatesAPI",
    "IngestionService",
    "JobsAPI",
    "LabService",
    "ModelService",
    "PromptsService",
    "RetrievalAPI",
    "RetrievalService",
    "ServingService",
    "SkillsService",
    "SubjectGrantsAPI",
    "SubjectsAPI",
    "ToolService",
]
