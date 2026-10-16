from gate import Gate


def run():
    gate = Gate({("a", "read"): False, ("b", "read"): True, ("c", "read"): True})
    gate.allowed("a", "read")
    gate.allowed("b", "read")
    gate.allowed("a", "read")
    gate.allowed("c", "read")
    gate.allowed("a", "read")
    return gate.reads


if __name__ == "__main__":
    print(run())
