from studio.models import PublishingAccount


def get_platform(platform):
    if platform == PublishingAccount.PLATFORM_BILIBILI:
        from .platforms.bilibili import BilibiliPlatform

        return BilibiliPlatform()
    if platform == PublishingAccount.PLATFORM_ACFUN:
        from .platforms.acfun import AcFunPlatform

        return AcFunPlatform()
    raise ValueError(f"不支持的发布平台：{platform}")
