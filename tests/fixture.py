from dataclasses import dataclass


@dataclass
class Widget:
    value: int

    def render(self) -> str:
        return helper(self.value)


def helper(value: int) -> str:
    return str(value)
