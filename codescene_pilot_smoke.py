"""Throwaway smoke test for the CodeScene PR-check pilot (ENG-7796).

This file exists only to verify that CodeScene posts a Code Health delta
check + review on a pull request. It is NOT meant to be merged — the PR is
a draft and will be closed, and this file deleted.
"""


def _smoke_classify(values, threshold, mode, flags):
    # Intentionally gnarly (deep nesting / branching) so the Clean Code
    # Collective profile has something to flag in the PR review.
    result = []
    for v in values:
        if v is not None:
            if v > threshold:
                if mode == "strict":
                    if flags.get("double"):
                        result.append(v * 2)
                    else:
                        result.append(v)
                elif mode == "loose":
                    if flags.get("negate"):
                        result.append(-v)
                    else:
                        result.append(v + 1)
                else:
                    result.append(0)
            else:
                if mode == "strict":
                    result.append(-1)
                else:
                    result.append(None)
    return result

# touch to trigger pull_request.synchronize (ENG-7796)

# live-repro 427cd87
