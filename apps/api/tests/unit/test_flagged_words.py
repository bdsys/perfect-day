"""Which flagged words are still in an entry's text (follow-up 25.3)."""

from __future__ import annotations

from app.services.flagged_words import still_present


def test_a_word_still_in_the_text_stays():
    assert still_present(["Sam", "Zoo"], "Sam went home") == ["Sam"]


def test_a_word_inside_a_longer_word_does_not_count():
    assert still_present(["Sam"], "Samantha came over") == []


def test_case_matters():
    assert still_present(["Sam"], "sam came over") == []


def test_nothing_flagged_gives_nothing():
    assert still_present([], "Sam went home") == []


def test_duplicates_are_dropped_and_order_kept():
    assert still_present(["Zoo", "Sam", "Zoo"], "Sam at the Zoo") == ["Zoo", "Sam"]


def test_a_word_with_regex_characters_is_matched_literally():
    assert still_present(["C++"], "We learned C") == []
