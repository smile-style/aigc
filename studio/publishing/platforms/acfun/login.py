class AcFunLogin:
    @staticmethod
    def parse_cookie_header(raw_cookie):
        cookies = {}
        for item in str(raw_cookie or "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name and value:
                cookies[name] = value
        return cookies
