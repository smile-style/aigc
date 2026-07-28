from dataclasses import dataclass
from importlib import import_module

from studio.models import PublishingAccount


@dataclass(frozen=True)
class PlatformDefinition:
    key: str
    label: str
    factory_path: str
    login_mode: str
    title_limit: int
    requires_partition: bool = True

    def create(self):
        module_name, class_name = self.factory_path.rsplit(".", 1)
        return getattr(import_module(module_name), class_name)()


PLATFORMS = {
    PublishingAccount.PLATFORM_BILIBILI: PlatformDefinition(
        PublishingAccount.PLATFORM_BILIBILI,
        "Bilibili",
        "studio.publishing.platforms.bilibili.BilibiliPlatform",
        "qr",
        80,
    ),
    PublishingAccount.PLATFORM_ACFUN: PlatformDefinition(
        PublishingAccount.PLATFORM_ACFUN,
        "AcFun",
        "studio.publishing.platforms.acfun.AcFunPlatform",
        "cookie",
        50,
    ),
    PublishingAccount.PLATFORM_DOUYIN: PlatformDefinition(
        PublishingAccount.PLATFORM_DOUYIN,
        "抖音",
        "studio.publishing.platforms.douyin.DouyinPlatform",
        "oauth",
        55,
        requires_partition=False,
    ),
}


def get_platform(platform):
    try:
        definition = PLATFORMS[platform]
    except KeyError as exc:
        raise ValueError(f"不支持的发布平台：{platform}") from exc
    return definition.create()


def get_platform_definition(platform):
    try:
        return PLATFORMS[platform]
    except KeyError as exc:
        raise ValueError(f"不支持的发布平台：{platform}") from exc


def platform_definitions():
    return tuple(PLATFORMS.values())
