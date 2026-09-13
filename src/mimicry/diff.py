"""Render literal draft edits without interpreting generated text as HTML."""

import html
import re
from difflib import SequenceMatcher


def edit_difference(before: str, after: str) -> str:
    # Keep whitespace and punctuation; use individual Han characters for Chinese text.
    pattern = r"[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+(?:['’][^\W_\u3400-\u9fff]+)*|\s+|."
    old = re.findall(pattern, before, re.DOTALL)
    new = re.findall(pattern, after, re.DOTALL)
    pieces = []
    matcher = SequenceMatcher(lambda token: token.isspace(), old, new, autojunk=False)
    for operation, i, j, k, end in matcher.get_opcodes():
        if operation == "equal":
            pieces.append(html.escape("".join(old[i:j])))
        else:
            if operation in ("delete", "replace"):
                pieces.append('<del class="heist-removed">' + html.escape("".join(old[i:j]))
                              + '</del>')
            if operation in ("insert", "replace"):
                pieces.append('<ins class="heist-added">' + html.escape("".join(new[k:end]))
                              + '</ins>')
    return '<div class="heist-writing heist-diff">' + "".join(pieces) + '</div>'
