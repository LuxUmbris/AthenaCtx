class Memo:
    def __init__(self):
        self.values = {}

    def get(self, key, loader):
        value = self.values.get(key)
        if value:
            return value
        value = loader()
        self.values[key] = value
        return value
