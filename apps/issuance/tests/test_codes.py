from apps.issuance.codes import ALPHABET, CODE_LENGTH, generate_code, is_valid_format


def test_generated_codes_have_shape_and_valid_check_char():
    for _ in range(500):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(ALPHABET)
        assert is_valid_format(code)


def test_alphabet_has_no_lookalikes():
    assert not set("01OILU") & set(ALPHABET)
    assert len(ALPHABET) == 30


def test_format_accepts_grouping_case_and_spaces():
    code = generate_code()
    grouped = f"{code[:4]}-{code[4:8]}-{code[8:]}".lower()
    assert is_valid_format(grouped)
    assert is_valid_format(f" {code[:4]} {code[4:]} ")


def test_any_single_wrong_character_is_caught():
    code = generate_code()
    for i in range(CODE_LENGTH):
        for ch in ALPHABET:
            if ch != code[i]:
                assert not is_valid_format(code[:i] + ch + code[i + 1:])


def test_adjacent_swaps_caught():
    code = generate_code()
    caught = total = 0
    for i in range(CODE_LENGTH - 1):
        # Luhn mod N cannot catch a swap of the first and last symbols; skip that known pair.
        if code[i] != code[i + 1] and {code[i], code[i + 1]} != {ALPHABET[0], ALPHABET[-1]}:
            total += 1
            swapped = code[:i] + code[i + 1] + code[i] + code[i + 2:]
            caught += not is_valid_format(swapped)
    assert caught == total


def test_wrong_length_or_ambiguous_chars_rejected():
    code = generate_code()
    assert not is_valid_format(code[:-1])
    assert not is_valid_format(code + "2")
    assert not is_valid_format("0" + code[1:])
    assert not is_valid_format("")
