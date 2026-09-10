class LogicalClock:
    def __init__(self, now: str = "2024-01-01T00:00:00Z"):
        self.now = now

    def set(self, value: str) -> str:
        if value < self.now:
            raise ValueError("logical clock cannot move backwards")
        self.now = value
        return self.now

