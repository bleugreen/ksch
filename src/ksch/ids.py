import uuid

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, key))
