"""The known-bad code version the bad-deploy injection uploads (T15)."""


def handler(event, context):
    config = None
    print("ERROR handler failed after deploy: 'NoneType' object has no attribute 'get'")
    return config.get("backend")
