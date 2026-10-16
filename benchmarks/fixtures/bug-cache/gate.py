from cache import Memo


class Gate:
    def __init__(self, rules):
        self.rules = rules
        self.memo = Memo()
        self.reads = 0

    def allowed(self, actor, action):
        key = (actor, action)
        return self.memo.get(key, lambda: self._read(key))

    def _read(self, key):
        self.reads += 1
        return self.rules.get(key, False)
