"""Deterministic fake for the Dreamina Design MCP tool boundary."""


class FakeMcpToolInvoker:
    def __init__(self, responses):
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        values = self.responses.get(name, [])
        if not values:
            raise AssertionError(f"unexpected MCP tool call: {name}")
        return values.pop(0)
