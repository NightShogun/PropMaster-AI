"""Small translation helper used by UI and domain modules."""


def translate(language, en_text, ar_text):
    return en_text if language == "en" else ar_text
