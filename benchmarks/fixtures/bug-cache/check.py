from gate import Gate


def run():
    gate = Gate({("a", "read"): False, ("b", "read"): True})
    first = gate.allowed("a", "read")
    second = gate.allowed("a", "read")
    return first, second, gate.reads


if __name__ == "__main__":
    print(run())
