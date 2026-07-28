from .check import DouyinChecker
from .login import DouyinLogin
from .upload import DouyinUploader


class DouyinPlatform:
    def __init__(self):
        self.login = DouyinLogin()
        self.checker = DouyinChecker()
        self.uploader = DouyinUploader()
