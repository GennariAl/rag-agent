from typing import NewType

import aioboto3
import boto3
from dishka import Provider, Scope, provide
from types_aiobotocore_s3.service_resource import Bucket

from app.aws_settings import AWSSettings

AccountId = NewType("AccountId", str)
LandingBucket = NewType("LandingBucket", Bucket)


class AWSProvider(Provider):
    scope = Scope.APP

    @provide
    def session(self) -> aioboto3.Session:
        return aioboto3.Session()

    @provide
    def sync_session(self) -> boto3.Session:
        return boto3.Session()

    @provide
    async def account_id(
        self, session: aioboto3.Session, settings: AWSSettings
    ) -> AccountId:
        async with session.client("sts", endpoint_url=settings.aws_endpoint_url) as sts:
            identity = await sts.get_caller_identity()
            return AccountId(identity.get("Account"))
