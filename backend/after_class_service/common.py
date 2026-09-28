class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def string(value, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise Problem(400, '文字格式或长度不正确')
    return value.strip()
