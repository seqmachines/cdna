from __future__ import annotations

import os


def complete_text(model: str, system: str, prompt: str) -> str:
    """Call LiteLLM lazily so non-LLM CLI commands remain importable."""
    test_response = os.environ.get("CDNA_TEST_LLM_RESPONSE")
    if test_response is not None:
        return test_response

    try:
        from litellm import completion
    except Exception as exc:  # pragma: no cover - exercised in environments without litellm.
        raise RuntimeError(
            "LiteLLM is required for LLM calls. Install with `python3 -m pip install -e .` "
            "or `python3 -m pip install -r requirements.txt`."
        ) from exc

    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LiteLLM returned an empty response")
    return content
