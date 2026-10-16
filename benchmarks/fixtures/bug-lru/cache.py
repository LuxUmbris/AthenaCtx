from collections import OrderedDict


class Memo:
    def __init__(self, capacity):
        self.capacity = capacity
        self.values = OrderedDict()

    def get(self, key, loader):
        if key in self.values:
            value = self.values[key]
            if value:
                self.values.move_to_end(key)
            return value
        value = loader()
        self.values[key] = value
        if len(self.values) > self.capacity:
            self.values.popitem(last=False)
        return value
