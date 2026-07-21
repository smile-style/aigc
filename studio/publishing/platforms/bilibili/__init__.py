from .check import BilibiliChecker
from .login import BilibiliLogin
from .upload import BilibiliUploader


class BilibiliPlatform:
    def __init__(self):
        self.login = BilibiliLogin()
        self.checker = BilibiliChecker()
        self.uploader = BilibiliUploader()
