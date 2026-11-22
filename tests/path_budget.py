"""Path lengths as Windows' MAX_PATH counts them."""


def utf16_len(text: str) -> int:
    """UTF-16 code units: a character outside the BMP takes two."""
    return len(text.encode("utf-16-le")) // 2
