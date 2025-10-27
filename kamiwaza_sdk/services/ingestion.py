# kamiwaza_sdk/services/ingestion.py

from typing import List, Optional, Dict, Any
from .base_service import BaseService


class IngestionService(BaseService):
    """
    Service for Kamiwaza Ingestion operations.

    The ingestion service provides automated data source scanning and catalog population.
    Supports multiple data source types including S3, PostgreSQL, Kafka, and more.

    Operations:
        - ingest_s3() - Scan S3 bucket and create catalog entries
        - ingest_postgres() - Scan PostgreSQL database tables
        - run_ingestion() - Run ingestion with custom source type
        - get_job_status() - Check status of ingestion job
    """

    def ingest_s3(
        self,
        bucket: str,
        prefix: str = "",
        recursive: bool = True,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        **kwargs
    ) -> List[str]:
        """
        Ingest S3 bucket contents into the catalog.

        Scans the specified S3 bucket and automatically creates catalog datasets
        for each object found. Supports both AWS S3 and S3-compatible storage.

        Args:
            bucket: S3 bucket name to scan
            prefix: Optional prefix to filter objects (e.g., "data/")
            recursive: Whether to scan subdirectories recursively
            aws_access_key_id: AWS access key (uses default credentials if not provided)
            aws_secret_access_key: AWS secret key (uses default credentials if not provided)
            region_name: AWS region (default: us-east-1)
            endpoint_url: Custom S3 endpoint for MinIO or S3-compatible storage
            **kwargs: Additional S3 configuration options

        Returns:
            List of URNs for created datasets

        Example:
            >>> # Ingest entire bucket
            >>> urns = client.ingestion.ingest_s3(bucket="my-data-lake")

            >>> # Ingest specific prefix with credentials
            >>> urns = client.ingestion.ingest_s3(
            ...     bucket="my-bucket",
            ...     prefix="data/prod/",
            ...     recursive=True,
            ...     aws_access_key_id="AKIA...",
            ...     aws_secret_access_key="...",
            ...     region_name="us-west-2"
            ... )

            >>> # Use MinIO (S3-compatible storage)
            >>> urns = client.ingestion.ingest_s3(
            ...     bucket="test-bucket",
            ...     endpoint_url="http://localhost:9000",
            ...     aws_access_key_id="minioadmin",
            ...     aws_secret_access_key="minioadmin"
            ... )
        """
        # Build kwargs for S3 ingestion
        ingest_kwargs = {
            "bucket": bucket,
            "prefix": prefix,
            "recursive": recursive,
            **kwargs
        }

        # Add AWS credentials if provided
        if aws_access_key_id:
            ingest_kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            ingest_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if region_name:
            ingest_kwargs["region_name"] = region_name
        if endpoint_url:
            ingest_kwargs["endpoint_url"] = endpoint_url

        return self.run_ingestion(source_type="s3", **ingest_kwargs)

    def ingest_postgres(
        self,
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 5432,
        schema: Optional[str] = None,
        **kwargs
    ) -> List[str]:
        """
        Ingest PostgreSQL database tables into the catalog.

        Scans the specified PostgreSQL database and creates catalog datasets
        for each table found.

        Args:
            host: PostgreSQL host
            database: Database name
            user: Database user
            password: Database password
            port: PostgreSQL port (default: 5432)
            schema: Schema to scan (default: public)
            **kwargs: Additional PostgreSQL configuration

        Returns:
            List of URNs for created datasets

        Example:
            >>> urns = client.ingestion.ingest_postgres(
            ...     host="localhost",
            ...     database="mydb",
            ...     user="admin",
            ...     password="secret",
            ...     schema="public"
            ... )
        """
        ingest_kwargs = {
            "host": host,
            "database": database,
            "user": user,
            "password": password,
            "port": port,
            **kwargs
        }

        if schema:
            ingest_kwargs["schema"] = schema

        return self.run_ingestion(source_type="postgres", **ingest_kwargs)

    def run_ingestion(
        self,
        source_type: str,
        **kwargs
    ) -> List[str]:
        """
        Run ingestion with custom source type and configuration.

        Generic ingestion method that supports any configured data source type.
        For convenience, use the specific methods (ingest_s3, ingest_postgres, etc.)

        Args:
            source_type: Type of data source ("s3", "postgres", "kafka", "hive", etc.)
            **kwargs: Source-specific configuration parameters

        Returns:
            List of URNs for created datasets

        Raises:
            ValueError: If source_type is invalid
            KamiwazaAPIError: If ingestion fails

        Example:
            >>> urns = client.ingestion.run_ingestion(
            ...     source_type="kafka",
            ...     bootstrap_servers="localhost:9092",
            ...     topic_pattern=".*"
            ... )
        """
        payload = {
            "source_type": source_type,
            "kwargs": kwargs
        }

        response = self.client.post("/ingestion/ingest/run", json=payload)

        # Extract URNs from response
        if isinstance(response, dict):
            return response.get("urns", [])
        elif isinstance(response, list):
            return response
        else:
            return []

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get status of a running or completed ingestion job.

        Args:
            job_id: Job identifier

        Returns:
            Job status information

        Example:
            >>> status = client.ingestion.get_job_status("job-123")
            >>> print(f"Status: {status['status']}")
        """
        return self.client.get(f"/ingestion/ingest/status/{job_id}")

    def health_check(self) -> Dict[str, Any]:
        """
        Check if ingestion service is available.

        Returns:
            Health status information

        Example:
            >>> health = client.ingestion.health_check()
            >>> print(health["status"])
        """
        return self.client.get("/ingestion/health")
