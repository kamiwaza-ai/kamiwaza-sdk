# kamiwaza_sdk/schemas/models/external_endpoint.py

"""Typed request shapes for registering external (vendor-hosted) models.

These mirror the platform's submission contract at
``kamiwaza/services/models/endpoints/schemas/`` — the *inline* shape an
operator POSTs to ``/models/``, where the credential travels as a plaintext
``credential_secret`` blob that the platform normalizes into a Catalog secret
URN server-side. They are deliberately the pre-normalization shape: no
``credential_secret_urn`` field, since the SDK caller supplies the plaintext
credential separately via :meth:`ModelService.register_external_model`.
"""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Credentials — serialized to the inline ``credential_secret`` JSON string.
# ---------------------------------------------------------------------------


class AWSBedrockBearerCredential(BaseModel):
    """AWS Bedrock API key (bearer token) auth."""

    model_config = ConfigDict(extra="allow")

    auth_type: Literal["bearer"] = "bearer"
    bearer_token: str = Field(..., min_length=1)


class AWSBedrockIamCredential(BaseModel):
    """AWS IAM credentials — long-lived access key or STS-assumed-role."""

    model_config = ConfigDict(extra="allow")

    auth_type: Literal["iam"] = "iam"
    aws_access_key_id: str = Field(..., min_length=1)
    aws_secret_access_key: str = Field(..., min_length=1)
    aws_session_token: Optional[str] = None


class AWSTranscribeCredential(BaseModel):
    """AWS IAM credentials for Transcribe (access key or STS-assumed-role)."""

    model_config = ConfigDict(extra="allow")

    aws_access_key_id: str = Field(..., min_length=1)
    aws_secret_access_key: str = Field(..., min_length=1)
    aws_session_token: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints — the ``external_endpoint`` blob (minus the credential).
# ---------------------------------------------------------------------------


class AWSBedrockChatEndpoint(BaseModel):
    """AWS Bedrock chat endpoint via LiteLLM Converse."""

    model_config = ConfigDict(extra="allow")

    protocol: Literal["aws_bedrock"] = "aws_bedrock"
    region: str = Field(..., min_length=1, description="AWS region (e.g. us-east-1)")
    model_id: str = Field(
        ...,
        min_length=1,
        description="Bedrock model id (e.g. anthropic.claude-3-sonnet-20240229-v1:0).",
    )
    endpoint_url: Optional[str] = Field(
        default=None,
        description="Optional Bedrock runtime endpoint override (VPC PrivateLink, etc.).",
    )
    inference_profile_arn: Optional[str] = Field(
        default=None,
        description="Optional Bedrock inference-profile ARN (provisioned throughput).",
    )
    extra_body: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operator-set knobs forwarded to the provider (guardrails, etc.).",
    )
    display_name: Optional[str] = Field(default=None, max_length=200)


class AWSTranscribeEndpoint(BaseModel):
    """AWS Transcribe (speech-to-text) endpoint."""

    model_config = ConfigDict(extra="allow")

    protocol: Literal["aws_transcribe"] = "aws_transcribe"
    region: str = Field(
        ..., min_length=1, description="AWS region; must match the s3_bucket region."
    )
    s3_bucket: str = Field(
        ...,
        min_length=3,
        max_length=63,
        description="S3 bucket for transcription job audio + transcript artifacts.",
    )
    s3_prefix: str = Field(default="transcribe-jobs/", max_length=512)
    language_code: str = Field(
        default="",
        max_length=16,
        description="BCP-47 language code (e.g. en-US). Empty = auto-detect.",
    )
    media_format: str = Field(default="wav")
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    show_speaker_labels: bool = Field(default=False)
    max_speaker_labels: int = Field(default=2, ge=2, le=10)
    vocabulary_name: Optional[str] = Field(default=None, max_length=200)
    vocabulary_filter_name: Optional[str] = Field(default=None, max_length=200)
    vocabulary_filter_method: Optional[str] = None
    redact_pii: bool = Field(default=False)
    pii_entity_types: list[str] = Field(default_factory=list)
    job_poll_interval: float = Field(default=2.0, gt=0.0, le=60.0)
    job_timeout_seconds: float = Field(default=900.0, gt=0.0)
    display_name: Optional[str] = Field(default=None, max_length=200)
