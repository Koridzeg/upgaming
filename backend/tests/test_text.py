from investigator.text import content_hash, normalize_text


def test_normalize_folds_non_breaking_spaces_and_whitespace_runs():
    # The exact difference found between duplicate HotpotQA paragraphs.
    assert normalize_text("66\xa0km  south\n of\tNewcastle ") == "66 km south of Newcastle"


def test_normalize_folds_compatibility_characters():
    assert normalize_text("ﬁle") == "file"  # 'fi' ligature, common in PDF text


def test_hash_ignores_formatting_but_not_content():
    original = "It is 66 km south of Newcastle."
    assert content_hash(original) == content_hash("It is 66\xa0km  south of Newcastle.")
    assert content_hash(original) != content_hash("It is 67 km south of Newcastle.")
