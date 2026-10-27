"""Private screenshot storage. Long-lived credentials never leave the relay."""

from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
VIEW_URL_EXPIRES_SECONDS = 60 * 60


def validate_jpeg(data: bytes) -> None:
    if not data or len(data) > MAX_SCREENSHOT_BYTES:
        raise ValueError("截图大小应在 2MB 以内")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "JPEG" or not (1 <= image.width <= 4096 and 1 <= image.height <= 4096):
                raise ValueError("截图必须为有效的 JPEG 图片")
            if image.width * image.height > 8_000_000:
                raise ValueError("截图尺寸过大")
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("截图内容损坏") from exc


class ScreenshotStore:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        return all(
            (
                self.settings.cos_secret_id,
                self.settings.cos_secret_key,
                self.settings.cos_bucket,
                self.settings.cos_region,
                self.settings.cos_screenshot_domain,
            )
        )

    def _client(self, *, custom=False):
        from qcloud_cos import CosConfig, CosS3Client

        values = {
            "Region": self.settings.cos_region,
            "SecretId": self.settings.cos_secret_id,
            "SecretKey": self.settings.cos_secret_key,
            "Scheme": "https",
            "Timeout": 20,
        }
        if custom:
            domain = urlsplit(self.settings.cos_screenshot_domain)
            if domain.scheme != "https" or not domain.hostname or domain.path not in {"", "/"}:
                raise ValueError("截图域名配置不正确")
            values["Domain"] = domain.netloc
        return CosS3Client(CosConfig(**values), retry=0)

    def put(self, key: str, data: bytes):
        self._client().put_object(
            Bucket=self.settings.cos_bucket,
            Key=key,
            Body=data,
            ContentType="image/jpeg",
            CacheControl="private, max-age=300",
        )

    def signed_url(self, key: str):
        return self._client(custom=True).get_presigned_url(
            Bucket=self.settings.cos_bucket, Key=key, Method="GET", Expired=VIEW_URL_EXPIRES_SECONDS
        )

    def signed_put_url(self, key: str):
        # One object-scoped upload permission can be reused for a day's captures.
        return self._client().get_presigned_url(
            Bucket=self.settings.cos_bucket, Key=key, Method="PUT", Expired=86400
        )

    def exists(self, key: str) -> bool:
        self._client().head_object(Bucket=self.settings.cos_bucket, Key=key)
        return True
