from .check import AcFunChecker
from .login import AcFunLogin
from .upload import AcFunUploader


class AcFunPlatform:
    def __init__(self):
        self.login = AcFunLogin()
        self.checker = AcFunChecker()
        self.uploader = AcFunUploader()
