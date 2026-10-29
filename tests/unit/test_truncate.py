from coding_agent.sandbox import _Capture  # pyright: ignore[reportPrivateUsage]
from coding_agent.tools.bash import truncate


def test_short_text_is_unchanged():
    assert truncate("hello", 10) == "hello"


def test_long_text_keeps_head_and_tail_and_counts_the_cut():
    text = "a" * 50 + "b" * 100 + "c" * 50

    result = truncate(text, 100)

    assert result == "a" * 50 + "\n[... 100 characters cut ...]\n" + "c" * 50


def test_odd_limit_keeps_exactly_limit_characters():
    result = truncate("x" * 20, 7)

    assert result.count("x") == 7
    assert "13 characters cut" in result


def test_output_dropped_while_capturing_is_counted():
    assert truncate("ab" * 100, 100, already_cut=500) == (
        "ab" * 25 + "\n[... 600 characters cut ...]\n" + "ab" * 25
    )


def test_capture_keeps_everything_under_the_limit():
    capture = _Capture(10)
    capture.feed(b"abc")
    capture.feed(b"de")

    assert capture.text() == "abcde"
    assert capture.dropped == 0


def test_capture_keeps_head_and_tail_and_counts_the_middle():
    capture = _Capture(10)
    for chunk in (b"01234", b"56789abc", b"defgh"):
        capture.feed(chunk)

    assert capture.text() == "01234" + "defgh"
    assert capture.dropped == 8
    assert capture.tail_window() == b"defgh"
