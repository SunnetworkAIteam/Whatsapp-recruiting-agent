"""
app/resume_storage.py

Handles storing candidate resumes (or any file a candidate sends via
WhatsApp) in a private S3 bucket, and generating presigned download
URLs for the dashboard's Screening Details tab.

Real gap this closes: candidates regularly send their resume as a file
attachment during screening, but the system previously only read text
messages -- attachments were silently ignored entirely. Twilio's
inbound webhook DOES include media info (NumMedia, MediaUrl0,
MediaContentType0) when a message has an attachment; this module is
what actually downloads that file from Twilio and persists it
somewhere useful.

Twilio's own media URLs require HTTP Basic Auth (account_sid as
username, auth_token as password) and are not permanently public/
guaranteed to stay available -- so the file is re-hosted in S3
immediately upon receipt, rather than just storing Twilio's URL
directly.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import boto3
import requests
from botocore.exceptions import ClientError

logger = logging.getLogger("app.resume_storage")

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.environ.get("AWS_S3_BUCKET_NAME")
AWS_S3_REGION = os.environ.get("AWS_S3_REGION", "ap-south-2")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")


class ResumeStorageError(Exception):
    """Raised when a resume can't be downloaded from Twilio or uploaded to S3."""


def _get_s3_client():
    if not (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_S3_BUCKET_NAME):
        raise ResumeStorageError(
            "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, or AWS_S3_BUCKET_NAME "
            "not set in the environment."
        )
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_S3_REGION,
    )


def _guess_extension(content_type: str) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }
    return mapping.get(content_type, "")


def download_and_store_resume(
    twilio_media_url: str,
    content_type: str,
    candidate_id: str,
) -> str:
    """
    Downloads a media file from Twilio (using account SID/auth token for
    Basic Auth, since Twilio media URLs aren't publicly fetchable
    without it) and re-uploads it to the private S3 bucket, keyed by
    candidate ID so files never collide between candidates.

    Returns the S3 object key (not a public URL -- see
    generate_resume_download_url() for that, since the bucket is
    private and links must be short-lived presigned URLs).
    """
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        raise ResumeStorageError("TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set.")

    response = requests.get(
        twilio_media_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=30,
    )
    if response.status_code != 200:
        raise ResumeStorageError(
            f"Failed to download media from Twilio (status={response.status_code})."
        )

    extension = _guess_extension(content_type)
    object_key = f"resumes/{candidate_id}/{uuid.uuid4().hex}{extension}"

    s3 = _get_s3_client()
    try:
        s3.put_object(
            Bucket=AWS_S3_BUCKET_NAME,
            Key=object_key,
            Body=response.content,
            ContentType=content_type or "application/octet-stream",
        )
    except ClientError as exc:
        raise ResumeStorageError(f"Failed to upload resume to S3: {exc}") from exc

    logger.info("Stored resume for candidate %s at s3://%s/%s", candidate_id, AWS_S3_BUCKET_NAME, object_key)
    return object_key


def generate_resume_download_url(object_key: str, expires_in: int = 3600) -> Optional[str]:
    """
    Generates a presigned, time-limited download URL for a resume
    stored in the private S3 bucket (default: valid for 1 hour). The
    dashboard calls this fresh each time it needs to show a download
    link, rather than storing a long-lived URL, since the bucket is
    intentionally private.
    """
    if not object_key:
        return None
    try:
        s3 = _get_s3_client()
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": object_key},
            ExpiresIn=expires_in,
        )
    except (ClientError, ResumeStorageError) as exc:
        logger.error("Failed to generate presigned URL for %s: %s", object_key, exc)
        return None