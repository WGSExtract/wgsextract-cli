def parse_thread_count(value: object) -> int | None:
    """Parse a --threads / cpu_threads value; None means "auto"."""
    if value is None or (isinstance(value, str) and value.strip().lower() == "auto"):
        return None
    try:
        count = int(str(value).strip())
    except ValueError:
        count = 0
    if count < 1:
        raise ValueError(f"expected a positive integer or 'auto', got {value!r}")
    return count
