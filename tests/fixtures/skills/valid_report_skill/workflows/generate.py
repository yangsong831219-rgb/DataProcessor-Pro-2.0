"""Non-executed fixture entrypoint for report_backend manifest tests."""


def generate(context):
    """Return a minimal success payload if a future test explicitly calls it."""
    return {"status": "succeeded", "context": context}
