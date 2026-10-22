from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import BaseClient

from config import settings


@lru_cache
def get_s3_client() -> BaseClient:
    client_options: dict[str, str] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        client_options["aws_access_key_id"] = settings.aws_access_key_id
        client_options["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.s3_endpoint_url:
        client_options["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **client_options)


def upload_fileobj(
    file_obj: BinaryIO,
    object_key: str,
    content_type: str,
) -> None:
    get_s3_client().upload_fileobj(
        file_obj,
        settings.s3_bucket,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )


def delete_object(object_key: str) -> None:
    get_s3_client().delete_object(Bucket=settings.s3_bucket, Key=object_key)


def create_presigned_url(object_key: str) -> str:
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": object_key},
        ExpiresIn=settings.s3_presigned_url_expiration,
    )
