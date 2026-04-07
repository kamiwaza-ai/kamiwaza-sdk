"""S3 read/write/lock operations for publishing extension catalog entries.

Handles the full publish lifecycle: lock acquisition, backup, download,
merge, upload, verification, preview image upload, and lock release.
Works with any S3-compatible storage (Cloudflare R2, AWS S3, MinIO).
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.profile_manager import PublishProfile
from kamiwaza_extensions.registry_builder import RegistryBuilder

console = Console(stderr=True)

# Maps extension_type to the catalog JSON filename.
_TYPE_FILE_MAP: Dict[str, str] = {
    "app": "apps.json",
    "tool": "tools.json",
    "service": "apps.json",  # Services are treated as apps in the catalog
}

# Lock time-to-live in seconds.  If a lock is older than this, it is
# considered stale and will be automatically cleaned up.
# Assumption: both the lock writer and the staleness checker use UTC
# wall-clock time.  NTP clock jumps or manual clock changes may cause
# premature or delayed cleanup — use a generous TTL margin for CI.
LOCK_TTL_SECONDS = int(os.environ.get("KZ_LOCK_TTL_SECONDS", "600"))


@dataclass
class PublishResult:
    """Outcome of a catalog publish operation."""

    extension_name: str
    version: str
    action: str  # "insert" or "replace"
    registry_url: str  # Where images were pushed
    catalog_file: str  # S3 key of the updated file
    images_pushed: List[str]
    dry_run: bool
    backup_path: Optional[Path] = None


class CatalogPublishError(RuntimeError):
    """A catalog publish operation failed."""

    pass


class CatalogPublisher:
    """Publish catalog entries to S3-compatible storage.

    Manages the full publish lifecycle including atomic locking,
    backup/restore, and upload verification.
    """

    def __init__(
        self,
        profile: PublishProfile,
        repo_version: int = 2,
        extension_dir: Optional[Path] = None,
    ) -> None:
        """Initialize S3 client from profile credentials.

        Args:
            profile: Publish profile with S3 endpoint and credentials.
            repo_version: Catalog schema version (determines garden path).
            extension_dir: Root directory of the extension project.  Used
                for placing backup files.  Falls back to ``Path.cwd()`` if
                not provided.
        """
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError:
            raise ImportError(
                "boto3 is required for catalog publishing. "
                "Install it with: pip install boto3  "
                "(or: pip install kamiwaza-sdk[publish])"
            )

        self._boto3 = boto3
        self._ClientError = ClientError

        self._profile = profile
        self._repo_version = repo_version
        self._extension_dir = extension_dir if extension_dir is not None else Path.cwd()

        # Build garden directory incorporating the optional catalog_prefix.
        prefix = profile.catalog_prefix.strip("/")
        if prefix:
            self._garden_dir = f"{prefix}/garden/v{repo_version}/"
        else:
            self._garden_dir = f"garden/v{repo_version}/"

        self._builder = RegistryBuilder()
        self._s3 = self._create_s3_client(profile)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(
        self,
        entry: Dict[str, Any],
        extension_type: str,
        force: bool = False,
        dry_run: bool = False,
        preview_image_path: Optional[Path] = None,
    ) -> PublishResult:
        """Full publish flow: lock, backup, download, merge, upload, verify, unlock.

        Args:
            entry: Catalog entry dict (from ``RegistryBuilder.build_entry``).
            extension_type: One of ``"app"``, ``"tool"``, or ``"service"``.
            force: Overwrite existing entry with same version.
            dry_run: Perform merge logic but skip all S3 writes.
            preview_image_path: Local path to preview image to upload.

        Returns:
            A ``PublishResult`` describing what was done.

        Raises:
            CatalogPublishError: On lock contention, upload failure, or
                verification mismatch.
            ValueError: On invalid extension_type or merge rejection.
        """
        if extension_type not in _TYPE_FILE_MAP:
            raise ValueError(
                f"Invalid extension_type '{extension_type}'. "
                f"Must be one of: {', '.join(_TYPE_FILE_MAP)}"
            )

        type_file = _TYPE_FILE_MAP[extension_type]
        s3_key = f"{self._garden_dir}{type_file}"
        backup_path: Optional[Path] = None

        if dry_run:
            return self._dry_run_publish(entry, type_file, s3_key, force)

        self._acquire_lock()
        try:
            # Backup current state
            backup_path = self._backup_current(type_file)

            # Download, merge, upload
            existing = self._download_entries(type_file)
            merged, action = self._builder.merge_into_registry(
                entry, existing, force=force,
            )
            self._upload_entries(type_file, merged)

            # Verify upload
            if not self._verify_upload(type_file, merged):
                console.print(
                    "[red]Upload verification failed, restoring...[/red]"
                )
                if backup_path is not None:
                    self._restore_backup(type_file, backup_path)
                else:
                    # First publish — no backup to restore; delete the bad file
                    try:
                        s3_key = f"{self._garden_dir}{type_file}"
                        self._s3.delete_object(
                            Bucket=self._profile.catalog_bucket, Key=s3_key,
                        )
                    except Exception:
                        pass
                raise CatalogPublishError(
                    "Upload verification failed: re-downloaded content does not "
                    "match what was uploaded."
                )

            # Upload preview image
            images_pushed: List[str] = []
            if preview_image_path is not None:
                remote_name = entry.get("preview_image", preview_image_path.name)
                # Strip any leading "images/" since _upload_preview_image adds the prefix
                if remote_name.startswith("images/"):
                    remote_name = remote_name[len("images/"):]
                try:
                    self._upload_preview_image(preview_image_path, remote_name)
                    images_pushed.append(remote_name)
                except Exception as img_exc:
                    console.print(
                        f"[red]Preview image upload failed: {img_exc}[/red]"
                    )
                    # Roll back the catalog entry
                    if backup_path is not None:
                        self._restore_backup(type_file, backup_path)
                    else:
                        try:
                            s3_key = f"{self._garden_dir}{type_file}"
                            self._s3.delete_object(
                                Bucket=self._profile.catalog_bucket, Key=s3_key,
                            )
                        except Exception:
                            pass
                    raise CatalogPublishError(
                        f"Preview image upload failed: {img_exc}"
                    ) from img_exc

            return PublishResult(
                extension_name=entry.get("name", ""),
                version=entry.get("version", ""),
                action=action,
                registry_url=self._profile.registry,
                catalog_file=s3_key,
                images_pushed=images_pushed,
                dry_run=False,
                backup_path=backup_path,
            )
        except CatalogPublishError:
            # CatalogPublishError from verify failure already restored backup.
            raise
        except Exception:
            # On any other failure, attempt to restore backup before re-raising.
            if backup_path is not None:
                try:
                    self._restore_backup(type_file, backup_path)
                    console.print("[yellow]Restored backup after publish failure.[/yellow]")
                except Exception as restore_exc:
                    console.print(
                        f"[red]CRITICAL: failed to restore backup at "
                        f"{backup_path} — catalog may be in an inconsistent state. "
                        f"Restore manually from the backup file. Error: {restore_exc}[/red]"
                    )
            raise
        finally:
            self._release_lock()

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> None:
        """Acquire the publish lock via atomic S3 put (IfNoneMatch).

        Before attempting the conditional PUT, checks for stale locks
        that have exceeded ``LOCK_TTL_SECONDS`` and removes them.

        Note: There is a known TOCTOU window between stale lock deletion
        and the conditional PUT.  Two processes may both detect a stale
        lock, both delete it, and both attempt acquisition.  The
        ``IfNoneMatch='*'`` conditional PUT ensures only one succeeds —
        the other receives PreconditionFailed and reports the conflict.
        This is safe; at worst a stale lock cleanup races benignly.

        Raises:
            CatalogPublishError: If the lock is already held.
        """
        lock_key = f"{self._garden_dir}registry.lock"
        lock_body = json.dumps({
            "owner": os.environ.get("CI_JOB_ID")
            or os.environ.get("GITHUB_RUN_ID")
            or "manual",
            "hostname": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "lock_ttl_seconds": LOCK_TTL_SECONDS,
        })

        # Check for and clean up stale locks before attempting acquisition.
        self._cleanup_stale_lock(lock_key)

        try:
            self._s3.put_object(
                Bucket=self._profile.catalog_bucket,
                Key=lock_key,
                Body=lock_body.encode("utf-8"),
                ContentType="application/json",
                IfNoneMatch="*",
            )
            console.print("[dim]Lock acquired.[/dim]")
        except self._ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("PreconditionFailed", "412"):
                self._report_lock_holder(lock_key)
                raise CatalogPublishError(
                    "Publish lock is held by another process"
                ) from exc
            raise CatalogPublishError(
                f"Failed to acquire publish lock: {exc}"
            ) from exc

    def _cleanup_stale_lock(self, lock_key: str) -> None:
        """Delete the lock object if it has exceeded ``LOCK_TTL_SECONDS``."""
        try:
            resp = self._s3.get_object(
                Bucket=self._profile.catalog_bucket,
                Key=lock_key,
            )
            lock_data = json.loads(resp["Body"].read().decode("utf-8"))
            acquired_at_str = lock_data.get("acquired_at")
            if acquired_at_str is None:
                return
            acquired_at = datetime.fromisoformat(acquired_at_str)
            elapsed = (datetime.now(timezone.utc) - acquired_at).total_seconds()
            if elapsed > LOCK_TTL_SECONDS:
                console.print(
                    f"[yellow]Stale lock detected (age {elapsed:.0f}s > "
                    f"TTL {LOCK_TTL_SECONDS}s). Removing...[/yellow]"
                )
                self._s3.delete_object(
                    Bucket=self._profile.catalog_bucket,
                    Key=lock_key,
                )
        except self._ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                return  # No existing lock -- nothing to clean up
            # Non-fatal: if we can't read/delete we'll just let the
            # conditional PUT fail with the normal error path.
            console.print(
                f"[dim]Could not check for stale lock: {exc}[/dim]"
            )
        except (json.JSONDecodeError, ValueError):
            # Lock body is corrupted — treat as stale and remove it to
            # prevent permanent contention.
            console.print(
                "[yellow]Warning: corrupted lock file detected. Removing...[/yellow]"
            )
            try:
                self._s3.delete_object(
                    Bucket=self._profile.catalog_bucket,
                    Key=lock_key,
                )
            except Exception:
                pass

    def _release_lock(self) -> None:
        """Release the publish lock by deleting the lock object."""
        lock_key = f"{self._garden_dir}registry.lock"
        try:
            self._s3.delete_object(
                Bucket=self._profile.catalog_bucket,
                Key=lock_key,
            )
            console.print("[dim]Lock released.[/dim]")
        except self._ClientError as exc:
            console.print(f"[yellow]Warning: failed to release lock: {exc}[/yellow]")

    def _report_lock_holder(self, lock_key: str) -> None:
        """Read the current lock and print owner details."""
        try:
            resp = self._s3.get_object(
                Bucket=self._profile.catalog_bucket,
                Key=lock_key,
            )
            lock_data = json.loads(resp["Body"].read().decode("utf-8"))
            console.print("[red]Publish lock is already held:[/red]")
            console.print(f"  Owner:    {lock_data.get('owner', 'unknown')}")
            console.print(f"  Hostname: {lock_data.get('hostname', 'unknown')}")
            console.print(f"  Acquired: {lock_data.get('acquired_at', 'unknown')}")
            console.print(f"  PID:      {lock_data.get('pid', 'unknown')}")
        except (self._ClientError, json.JSONDecodeError, KeyError):
            console.print("[red]Publish lock is held (could not read details).[/red]")

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------

    def _backup_current(self, type_file: str) -> Optional[Path]:
        """Download current catalog file to a local backup.

        Returns:
            Path to the backup file, or None if the catalog file does not
            exist yet (first publish).
        """
        s3_key = f"{self._garden_dir}{type_file}"
        try:
            resp = self._s3.get_object(
                Bucket=self._profile.catalog_bucket,
                Key=s3_key,
            )
            body = resp["Body"].read()
        except self._ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                console.print(f"[dim]No existing {type_file} to back up.[/dim]")
                return None
            raise

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = (
            self._extension_dir / "build" / "registry-backups"
            / self._garden_dir / timestamp
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / type_file

        backup_path.write_bytes(body)
        console.print(f"[dim]Backup saved to {backup_path}[/dim]")
        return backup_path

    def _restore_backup(self, type_file: str, backup_path: Path) -> None:
        """Upload a backup file back to S3 to restore prior state."""
        s3_key = f"{self._garden_dir}{type_file}"
        body = backup_path.read_bytes()
        self._s3.put_object(
            Bucket=self._profile.catalog_bucket,
            Key=s3_key,
            Body=body,
            ContentType="application/json",
        )
        console.print(f"[yellow]Restored {type_file} from backup.[/yellow]")

    # ------------------------------------------------------------------
    # Download / upload / verify
    # ------------------------------------------------------------------

    def _download_entries(self, type_file: str) -> List[Dict[str, Any]]:
        """Download and parse the current catalog entries from S3.

        Returns an empty list if the file does not exist.
        """
        s3_key = f"{self._garden_dir}{type_file}"
        try:
            resp = self._s3.get_object(
                Bucket=self._profile.catalog_bucket,
                Key=s3_key,
            )
            body = resp["Body"].read().decode("utf-8")
            entries = json.loads(body)
            if not isinstance(entries, list):
                raise CatalogPublishError(
                    f"Expected JSON array in {s3_key}, got {type(entries).__name__}"
                )
            return entries
        except self._ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                return []
            raise

    def _upload_entries(self, type_file: str, entries: List[Dict[str, Any]]) -> None:
        """Upload merged catalog entries to S3."""
        s3_key = f"{self._garden_dir}{type_file}"
        body = json.dumps(entries, indent=2, ensure_ascii=False)
        self._s3.put_object(
            Bucket=self._profile.catalog_bucket,
            Key=s3_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        console.print(f"[dim]Uploaded {s3_key} ({len(entries)} entries).[/dim]")

    def _verify_upload(
        self, type_file: str, expected: List[Dict[str, Any]]
    ) -> bool:
        """Re-download the catalog file and verify it matches what was uploaded."""
        actual = self._download_entries(type_file)
        return actual == expected

    def _upload_preview_image(self, local_path: Path, remote_name: str) -> None:
        """Upload a preview image to the garden images directory."""
        if not local_path.exists():
            raise CatalogPublishError(
                f"Preview image not found: {local_path}"
            )

        s3_key = f"{self._garden_dir}images/{remote_name}"
        content_type = _guess_image_content_type(local_path)

        self._s3.put_object(
            Bucket=self._profile.catalog_bucket,
            Key=s3_key,
            Body=local_path.read_bytes(),
            ContentType=content_type,
        )
        console.print(f"[dim]Uploaded preview image to {s3_key}[/dim]")

    # ------------------------------------------------------------------
    # Dry-run
    # ------------------------------------------------------------------

    def _dry_run_publish(
        self,
        entry: Dict[str, Any],
        type_file: str,
        s3_key: str,
        force: bool,
    ) -> PublishResult:
        """Perform merge logic without any S3 writes.

        Note: this reads from S3 to check for version conflicts (GET
        requests are side-effect-free).  No PUT/DELETE calls are made.
        """
        console.print("[yellow]Dry run -- no S3 writes will be performed.[/yellow]")

        existing = self._download_entries(type_file)
        _merged, action = self._builder.merge_into_registry(
            entry, existing, force=force,
        )

        console.print(
            f"[dim]Would {action} '{entry.get('name')}' "
            f"v{entry.get('version')} in {s3_key}[/dim]"
        )

        return PublishResult(
            extension_name=entry.get("name", ""),
            version=entry.get("version", ""),
            action=action,
            registry_url=self._profile.registry,
            catalog_file=s3_key,
            images_pushed=[],
            dry_run=True,
        )

    # ------------------------------------------------------------------
    # S3 client factory
    # ------------------------------------------------------------------

    def _create_s3_client(self, profile: PublishProfile) -> Any:
        """Create a boto3 S3 client from the profile's credential spec."""
        creds = profile.catalog_credentials

        if creds.startswith("aws-profile:"):
            profile_name = creds[len("aws-profile:"):]
            session = self._boto3.Session(profile_name=profile_name)
        elif creds == "env":
            session = self._boto3.Session()
        elif creds == "sso":
            raise NotImplementedError(
                "SSO credential flow is not yet supported. "
                "Use 'aws-profile:<name>' with an SSO-configured profile, "
                "or 'env' with exported credentials instead."
            )
        else:
            raise ValueError(
                f"Unknown credential spec '{creds}'. "
                "Expected 'aws-profile:<name>', 'env', or 'sso'."
            )

        return session.client("s3", endpoint_url=profile.catalog_endpoint)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _guess_image_content_type(path: Path) -> str:
    """Return a Content-Type string for common image formats."""
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
